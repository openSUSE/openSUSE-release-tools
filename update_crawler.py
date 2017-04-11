#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2015 SUSE Linux GmbH
# Copyright (c) 2016 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import itertools
import logging
import sys
import urllib2
import time
from xml.etree import cElementTree as ET

import osc.conf
import osc.core
import rpm
import yaml
import re
from urllib import quote_plus

from osclib.memoize import memoize
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

OPENSUSE = 'openSUSE:Leap:42.3'
FACTORY = 'openSUSE:Factory'
SLE = 'SUSE:SLE-12-SP2:Update'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET

# http://stackoverflow.com/questions/312443/how-do-you-split-a-list-into-evenly-sized-chunks-in-python
def chunks(l, n):
    """ Yield successive n-sized chunks from l.
    """
    for i in xrange(0, len(l), n):
        yield l[i:i+n]

class UpdateCrawler(object):
    def __init__(self, from_prj, to_prj):
        self.from_prj = from_prj
        self.to_prj = to_prj
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        self.filter_lookup = set()
        self.caching = False
        self.dryrun = False
        self.skipped = {}
        self.submit_new = {}
        self.api = StagingAPI(
            osc.conf.config['apiurl'], project = to_prj)

        self.parse_lookup()

    # FIXME: duplicated from manager_42
    def latest_packages(self):
        data = self.cached_GET(makeurl(self.apiurl,
                                       ['project', 'latest_commits', self.from_prj]))
        lc = ET.fromstring(data)
        packages = set()
        for entry in lc.findall('{http://www.w3.org/2005/Atom}entry'):
            title = entry.find('{http://www.w3.org/2005/Atom}title').text
            if title.startswith('In '):
                packages.add(title[3:].split(' ')[0])
        return sorted(packages)

    @memoize()
    def _cached_GET(self, url):
        return self.retried_GET(url).read()

    def cached_GET(self, url):
        if self.caching:
            return self._cached_GET(url)
        return self.retried_GET(url).read()

    def retried_GET(self, url):
        try:
            return http_GET(url)
        except urllib2.HTTPError, e:
            if 500 <= e.code <= 599:
                print 'Retrying {}'.format(url)
                time.sleep(1)
                return self.retried_GET(url)
            raise e

    def get_project_meta(self, prj):
        url = makeurl(self.apiurl, ['source', prj, '_meta'])
        return self.cached_GET(url)

    def is_maintenance_project(self, prj):
        root = ET.fromstring(self.get_project_meta(prj))
        return root.get('kind', None) == 'maintenance_release'

    def _meta_get_packagelist(self, prj, deleted=None, expand=False):

        query = {}
        if deleted:
            query['deleted'] = 1
        if expand:
            query['expand'] = 1

        u = osc.core.makeurl(self.apiurl, ['source', prj], query)
        return self.cached_GET(u)

    def meta_get_packagelist(self, prj, deleted=None, expand=False):
        root = ET.fromstring(self._meta_get_packagelist(prj, deleted, expand))
        return [ node.get('name') for node in root.findall('entry') if not node.get('name') == '_product' and not node.get('name').startswith('_product:') and not node.get('name').startswith('patchinfo.') ]

    def _get_source_infos(self, project, packages):
        query = [ 'view=info' ]
        if packages:
            query += [ 'package=%s'%quote_plus(p) for p in packages ]

        return self.cached_GET(makeurl(self.apiurl,
                                ['source', project],
                                query))

    def get_source_infos(self, project, packages):
        ret = dict()
        for pkg_chunks in chunks(sorted(packages), 50):
            root = ET.fromstring(self._get_source_infos(project, pkg_chunks))
            for package in root.findall('sourceinfo'):
                if package.findall('error'):
                    continue
                ret[package.get('package')] = package
        return ret

    def _get_source_package(self, project, package, revision):
        opts = { 'view': 'info' }
        if revision:
            opts['rev'] = revision
        return self.cached_GET(makeurl(self.apiurl,
                                ['source', project, package], opts))

    def _find_existing_request(self, src_project, src_package, rev, dst_project,
                       dst_package):
        """Create a submit request."""
        states = ['new', 'review', 'declined', 'revoked']
        reqs = osc.core.get_exact_request_list(self.apiurl,
                                               src_project,
                                               dst_project,
                                               src_package,
                                               dst_package,
                                               req_type='submit',
                                               req_state=states)
        foundrev = False
        for r in reqs:
            for a in r.actions:
                srcrev = a.src_rev
                # sometimes requests only contain the decimal revision
                if re.match(r'^\d+$', srcrev) is not None:
                    xml = ET.fromstring(self._get_source_package(src_project,src_package, srcrev))
                    srcrev = xml.get('verifymd5')
                logging.debug('rev {}'.format(srcrev))
                if srcrev == rev:
                    logging.debug('{}: found existing request {}'.format(dst_package, r.reqid))
                    foundrev = True
        return foundrev

    def _submitrequest(self, src_project, src_package, rev, dst_project,
                       dst_package, msg):
        res = 0
        print "creating submit request", src_project, src_package, rev, dst_project, dst_package
        if not self.dryrun:
            res = osc.core.create_submit_request(self.apiurl,
                                                 src_project,
                                                 src_package,
                                                 dst_project,
                                                 dst_package,
                                                 orev=rev,
                                                 message=msg)
        return res

    def submitrequest(self, src_project, src_package, rev, dst_package, origin):
        """Create a submit request using the osc.commandline.Osc class."""
        dst_project = self.to_prj
        msg = 'Automatic request from %s by UpdateCrawler' % src_project
        if not self._find_existing_request(src_project, src_package, rev, dst_project, dst_package):
            return self._submitrequest(src_project, src_package, rev, dst_project,
                                   dst_package, msg)
        return 0

    def is_source_innerlink(self, project, package):
        try:
            root = ET.fromstring(
                self.cached_GET(makeurl(self.apiurl,
                                 ['source', project, package, '_link']
                )))
            if root.get('project') is None and root.get('cicount'):
                return True
        except urllib2.HTTPError, err:
            # if there is no link, it can't be a link
            if err.code == 404:
                return False
            raise

    def parse_lookup(self):
        self.lookup = yaml.safe_load(self._load_lookup_file())

    def _load_lookup_file(self):
        prj = self.to_prj
        return self.cached_GET(makeurl(self.apiurl,
                                ['source', prj, '00Meta', 'lookup.yml']))

    def follow_link(self, project, package, rev, verifymd5):
        #print "follow", project, package, rev
        # verify it's still the same package
        xml = ET.fromstring(self._get_source_package(project, package, rev))
        if xml.get('verifymd5') != verifymd5:
            return None
        xml = ET.fromstring(self.cached_GET(makeurl(self.apiurl,
                                             ['source', project, package],
                                             {
                                                 'rev': rev
                                             })))
        linkinfo =  xml.find('linkinfo')
        if not linkinfo is None:
            ret = self.follow_link(linkinfo.get('project'), linkinfo.get('package'), linkinfo.get('srcmd5'), verifymd5)
            if ret:
                project, package, rev = ret
        return (project, package, rev)

    def update_targets(self, targets, sources):

        # special case maintenance project. Only consider main
        # package names. The code later follows the link in the
        # source project then.
        if self.is_maintenance_project(self.from_prj):
            mainpacks = set()
            for package, sourceinfo in sources.items():
                if package.startswith('patchinfo.'):
                    continue
                files = set([node.text for node in sourceinfo.findall('filename')])
                if '{}.spec'.format(package) in files:
                    mainpacks.add(package)

            sources = { package: sourceinfo for package, sourceinfo in sources.iteritems() if package in mainpacks }

        for package, sourceinfo in sources.items():

            origin = self.lookup.get(package, '')
            if self.filter_lookup and not origin in self.filter_lookup:
                if not origin.startswith('subpackage of'):
                    self.skipped.setdefault(origin, set()).add(package)
                continue

            if not package in targets:
                if not self.submit_new:
                    logging.info('Package %s not found in targets' % (package))
                    continue

                if self.is_source_innerlink(self.from_prj, package):
                    logging.debug('Package %s is sub package' % (package))
                    continue

            else:
                targetinfo = targets[package]

                # XXX: make more generic :-)
                devel_prj = self.api.get_devel_project(FACTORY, package)
                if devel_prj == 'devel:languages:haskell':
                    logging.info('skipping haskell package %s' % package)
                    continue

                # Compare verifymd5
                md5_from = sourceinfo.get('verifymd5')
                md5_to = targetinfo.get('verifymd5')
                if md5_from == md5_to:
                    #logging.info('Package %s not marked for update' % package)
                    continue

                if self.is_source_innerlink(self.to_prj, package):
                    logging.debug('Package %s is sub package' % (package))
                    continue

#            this makes only sense if we look at the expanded view
#            and want to submit from proper project
#            originproject = default_origin
#            if not sourceinfo.find('originproject') is None:
#                originproject = sourceinfo.find('originproject').text
#                logging.warn('changed originproject for {} to {}'.format(package, originproject))

            src_project, src_package, src_rev = self.follow_link(self.from_prj, package,
                                                                 sourceinfo.get('srcmd5'),
                                                                 sourceinfo.get('verifymd5'))

            res = self.submitrequest(src_project, src_package, src_rev, package, origin)
            if res:
                logging.info('Created request %s for %s' % (res, package))
            elif res != 0:
                logging.error('Error creating the request for %s' % package)

    def crawl(self, packages):
        """Main method of the class that run the crawler."""
        targets = self.get_source_infos(self.to_prj, packages)
        sources = self.get_source_infos(self.from_prj, packages)
        self.update_targets(targets, sources)

def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.osc_debug

    # initialize stagingapi config
    Config(args.to_prj)

    uc = UpdateCrawler(args.from_prj, args.to_prj)
    uc.caching = args.cache_requests
    uc.dryrun = args.dry
    uc.submit_new = args.new
    if args.only_from:
        for prj in args.only_from:
            uc.filter_lookup.add(prj)

    given_packages = args.packages
    if not given_packages:
        if args.all:
            given_packages = uc.meta_get_packagelist(args.from_prj)
        else:
            given_packages = uc.latest_packages()
    uc.crawl(given_packages)

    if uc.skipped:
        from pprint import pformat
        logging.debug("skipped packages: %s", pformat(uc.skipped))



if __name__ == '__main__':
    description = 'Create update SRs for Leap.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-a', '--all', action='store_true',
                        help='check all packages')
    parser.add_argument('-n', '--dry', action='store_true',
                        help='dry run, no POST, PUT, DELETE')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % SLE,
                        default=SLE)
    parser.add_argument('-t', '--to', dest='to_prj', metavar='PROJECT',
                        help='project where to submit the updates to (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('--only-from', dest='only_from', metavar='PROJECT', action ='append',
                        help='only submit packages that came from PROJECT')
    parser.add_argument("--osc-debug", action="store_true", help="osc debug output")
    parser.add_argument("--new", action="store_true", help="also submit new packages")
    parser.add_argument('--cache-requests', action='store_true', default=False,
                        help='cache GET requests. Not recommended for daily use.')
    parser.add_argument("packages", nargs='*', help="packages to check")

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    if args.dry:
        def dryrun(t, *args, **kwargs):
            return lambda *args, **kwargs: logging.debug("dryrun %s %s %s", t, args, str(kwargs)[:200])

        http_POST = dryrun('POST')
        http_PUT = dryrun('PUT')
        http_DELETE = dryrun('DELETE')

    sys.exit(main(args))

# vim: sw=4 et
