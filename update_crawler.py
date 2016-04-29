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

from osclib.memoize import memoize

OPENSUSE = 'openSUSE:Leap:42.2'
FACTORY = 'openSUSE:Factory'
SLE = 'SUSE:SLE-12-SP1:Update'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET


class UpdateCrawler(object):
    def __init__(self, from_prj, to_prj):
        self.from_prj = from_prj
        self.to_prj = to_prj
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        self.parse_lookup()
        self.filter_lookup = set()

    def retried_GET(self, url):
        try:
            return http_GET(url)
        except urllib2.HTTPError, e:
            if 500 <= e.code <= 599:
                print 'Retrying {}'.format(url)
                time.sleep(1)
                return self.retried_GET(url)
            raise e

    def _get_source_infos(self, project):
        return self.retried_GET(makeurl(self.apiurl,
                                ['source', project],
                                {
                                    'view': 'info'
                                })).read()

    def get_source_infos(self, project):
        root = ET.fromstring(self._get_source_infos(project))
        ret = dict()
        for package in root.findall('sourceinfo'):
            # skip packages that come via project link
            # FIXME: OBS needs to implement expand=0 for view=info
            if not package.find('originproject') is None:
                continue
            ret[package.get('package')] = package
        return ret

    def _submitrequest(self, src_project, src_package, rev, dst_project,
                       dst_package, msg):
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
                if a.to_xml().find('source').get('rev') == rev:
                    logging.debug('found existing request {}'.format(r.req_id))
                    foundrev = True
        res = 0
        if not foundrev:
            print "creating submit request", src_project, src_package, rev, dst_project, dst_package
            # XXX
            return 0
            res = osc.core.create_submit_request(self.apiurl,
                                                 src_project,
                                                 src_package,
                                                 dst_project,
                                                 dst_package,
                                                 orev=rev,
                                                 message=msg)
        return res

    def submitrequest(self, src_project, src_package, rev, dst_package):
        """Create a submit request using the osc.commandline.Osc class."""
        dst_project = self.to_prj
        msg = 'Automatic request from %s by UpdateCrawler' % src_project
        return self._submitrequest(src_project, src_package, rev, dst_project,
                                   dst_package, msg)

    def is_source_innerlink(self, project, package):
        try:
            root = ET.parse(
                http_GET(makeurl(self.apiurl,
                                 ['source', project, package, '_link']
                ))).getroot()
            if root.get('project') is None and root.get('cicount'):
                return True
        except urllib2.HTTPError, err:
            # if there is no link, it can't be a link
            if err.code == 404:
                return False
            raise

    def parse_lookup(self):
        self.lookup = yaml.load(self._load_lookup_file())

    def _load_lookup_file(self):
        return http_GET(makeurl(self.apiurl,
                                ['source', self.to_prj, '00Meta', 'lookup.yml']))

    def follow_link(self, project, package, rev, verifymd5):
        #print "follow", project, package, rev
        # verify it's still the same package
        xml = ET.fromstring(http_GET(makeurl(self.apiurl,
                                             ['source', project, package],
                                             {
                                                 'rev': rev,
                                                 'view': 'info'
                                             })).read())
        if xml.get('verifymd5') != verifymd5:
            return None
        xml = ET.fromstring(http_GET(makeurl(self.apiurl,
                                             ['source', project, package],
                                             {
                                                 'rev': rev
                                             })).read())
        linkinfo =  xml.find('linkinfo')
        if not linkinfo is None:
            ret = self.follow_link(linkinfo.get('project'), linkinfo.get('package'), linkinfo.get('srcmd5'), verifymd5)
            if ret:
                project, package, rev = ret
        return (project, package, rev)

    def update_targets(self, targets, sources):
        for package, sourceinfo in sources.items():
            if self.filter_lookup and not self.lookup.get(package, '') in self.filter_lookup:
                continue

            if not package in targets:
                logging.debug('Package %s not found in targets' % (package))
                continue

            targetinfo = targets[package]

            #if package != 'openssl':
            #    continue

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

            res = self.submitrequest(src_project, src_package, src_rev, package)
            if res:
                logging.info('Created request %s for %s' % (res, package))
            elif res != 0:
                logging.error('Error creating the request for %s' % package)


    def crawl(self):
        """Main method of the class that run the crawler."""
        targets = self.get_source_infos(self.to_prj)
        sources = self.get_source_infos(self.from_prj)
        self.update_targets(targets, sources)


def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.osc_debug

    uc = UpdateCrawler(args.from_prj, args.to_prj)
    if args.only_from:
        uc.filter_lookup.add(args.only_from)

    uc.crawl()

if __name__ == '__main__':
    description = 'Create update SRs for Leap.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-n', '--dry', action='store_true',
                        help='dry run, no POST, PUT, DELETE')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % SLE,
                        default=SLE)
    parser.add_argument('-t', '--to', dest='to_prj', metavar='PROJECT',
                        help='project where to submit the updates to (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('--only-from', dest='only_from', metavar='PROJECT',
                        help='only submit packages that came from PROJECT')
    parser.add_argument("--osc-debug", action="store_true", help="osc debug output")

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
