#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2015 SUSE Linux GmbH
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
from xml.etree import cElementTree as ET

import osc.conf
import osc.core
import urllib2
import sys
import time

from osclib.memoize import memoize

OPENSUSE = 'openSUSE:42'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_DELETE = osc.core.http_DELETE
http_PUT = osc.core.http_PUT
http_POST = osc.core.http_POST

class UpdateCrawler(object):
    def __init__(self, from_prj, caching = True):
        self.from_prj = from_prj
        self.caching = caching
        self.apiurl = osc.conf.config['apiurl']
        self.project_preference_order = [
                'SUSE:SLE-12-SP1:Update',
                'SUSE:SLE-12-SP1:GA',
                'SUSE:SLE-12:Update',
                'SUSE:SLE-12:GA',
                'openSUSE:Factory',
                ]
        self.subprojects = [
                '%s:SLE-Pkgs-With-Overwrites' % self.from_prj,
                '%s:Factory-Copies' % self.from_prj,
                '%s:SLE12-Picks' % self.from_prj,
                ]
        self.projects = [self.from_prj] + self.subprojects

        self.project_mapping = {}
        for prj in self.project_preference_order:
            if prj.startswith('SUSE:'):
                self.project_mapping[prj] = self.from_prj + ':SLE12-Picks'
            else:
                self.project_mapping[prj] = self.from_prj + ':Factory-Copies'

        self.reload_packages()
        self.pending_requests = []
        self.pending_requests = self.get_requests_list(self.from_prj)

    def reload_packages(self):
        self.packages = dict()
        for project in self.projects + self.project_preference_order:
            self.packages[project] = self.get_source_packages(project)

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

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        root = ET.fromstring(
            self.cached_GET(makeurl(self.apiurl,
                             ['source', project],
                             query=query)))
        packages = [i.get('name') for i in root.findall('entry')]
        return packages

    def _get_source_package(self, project, package, revision):
        opts = { 'view': 'info' }
        if revision:
            opts['rev'] = revision
        return self.cached_GET(makeurl(self.apiurl,
                                ['source', project, package], opts))


    def get_requests_list(self, project):
        """ Return package name of pending review requests """
        requests = []
        target = "action[target/@project='{}']".format(project)
        query = "match=({})+and+({})".format("state/@name='new'+or+state/@name='review'", target)
        url = makeurl(self.apiurl,['search', 'request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            action = rq.findall('action')
            action = action[0] # take the first one only
            requests.append(action.find('target').get('package'))

        return requests

    def get_request_infos(self, requestid):
        request = self.cached_GET(makeurl(self.apiurl, ['request', requestid]))
        root = ET.fromstring(request)
        action = root.find('.//action')
        source = action.find('source')
        target = action.find('target')
        project = source.get('project')
        package = source.get('package')
        rev = source.get('rev')
        return ( project, package, rev, target.get('package') )

    def remove_packages(self, project, packages):
        for package in packages:
            if not package in self.packages[project]:
                continue
            logging.info("deleting %s/%s", project, package)
            url = makeurl(self.apiurl, ['source', project, package])
            try:
                http_DELETE(url)
                self.packages[project].remove(package)
            except urllib2.HTTPError, err:
                if err.code == 404:
                    # not existant package is ok, we delete them all
                    pass
                else:
                    # If the package was there but could not be delete, raise the error
                    raise

    # copied from stagingapi - but the dependencies are too heavy
    def create_package_container(self, project, package):
        """
        Creates a package container without any fields in project/package
        :param project: project to create it
        :param package: package name
        """
        dst_meta = '<package name="{}"><title/><description/></package>'
        dst_meta = dst_meta.format(package)

        url = makeurl(self.apiurl, ['source', project, package, '_meta'])
        logging.debug("create %s/%s", project, package)
        http_PUT(url, data=dst_meta)
        self.packages[project].append(package)

    def _link_content(self, sourceprj, sourcepkg, rev):
        root = ET.fromstring(self._get_source_package(sourceprj, sourcepkg, rev))
        srcmd5 = root.get('srcmd5')
        vrev = root.get('vrev')
        if vrev is None:
            vrev = ''
        else:
            vrev = " vrev='{}'".format(vrev)
        link = "<link project='{}' package='{}' rev='{}'{}/>"
        return link.format(sourceprj, sourcepkg, srcmd5, vrev)

    def upload_link(self, project, package, link_string):
        url = makeurl(self.apiurl, ['source', project, package, '_link'])
        http_PUT(url, data=link_string)

    def link_packages(self, sourceprj, sourcepkg, sourcerev, targetprj, targetpkg):
        logging.info("update link %s/%s -> %s/%s@%s", targetprj, targetpkg, sourceprj, sourcepkg, sourcerev)

        # check do it have pending review request against this package
        if targetpkg in self.pending_requests:
            logging.info("There is a pending request to %s / %s, ignore!"%(self.from_prj, packages))
            return

        packages = [ targetpkg ]
        
        # make sure 'packages' covers all subpackages
        subpackages, mainpackage = self.get_specfiles(self.from_prj, targetpkg)
        if subpackages:
            if mainpackage and mainpackage != targetpkg:
                raise Exception("{} != {}".format(mainpackage, targetpkg))

            packages = list(set([ mainpackage ] ) | set(subpackages))

        # remove targetpkg in all subprojects where it existed before
        for project in self.subprojects:
            if not targetpkg in self.packages[project]:
                continue
            s, m = self.get_specfiles(project, targetpkg)
            pkgs = []
            if s:
                pkgs += s
            if m:
                pkgs += [m]
            self.remove_packages(project, pkgs)

        self.create_package_container(targetprj, targetpkg)
        link = self._link_content(sourceprj, sourcepkg, sourcerev)
        self.upload_link(targetprj, targetpkg, link)

        for package in [ p for p in packages if p != targetpkg ]:
            # FIXME: link packages from factory that override sle
            # ones to different project
            logging.debug("linking %s -> %s", package, targetpkg)
            link = "<link cicount='copy' package='{}' />".format(targetpkg)
            self.create_package_container(targetprj, package)
            self.upload_link(targetprj, package, link)

        self.remove_packages(self.from_prj, packages)

    def _filter_given_packages(self, packages):
        if packages:
            return [p for p in packages if p in self.packages[self.from_prj]]

        return self.packages[self.from_prj][:]

    def crawl(self, given_packages = None):
        """Main method of the class that run the crawler."""

        requests = dict()

        self.try_to_find_left_packages(self._filter_given_packages(given_packages))
        self.reload_packages()
        self.cleanup_and_freeze_links(self.from_prj, self._filter_given_packages(given_packages))

    def check_source_in_project(self, project, package, verifymd5):
        if not package in self.packages[project]:
            return None

        try:
            his = self.cached_GET(makeurl(self.apiurl,
                                   ['source', project, package, '_history']))
        except urllib2.HTTPError:
            return None

        his = ET.fromstring(his)
        revs = list()
        for rev in his.findall('revision'):
            revs.append(rev.find('srcmd5').text)
        revs.reverse()
        for i in range(min(len(revs), 5)): # check last 5 commits
            srcmd5=revs.pop(0)
            root = self.cached_GET(makeurl(self.apiurl,
                                    ['source', project, package], { 'rev': srcmd5, 'view': 'info'}))
            root = ET.fromstring(root)
            if root.get('verifymd5') == verifymd5:
                return srcmd5
        return None

    # check if we can find the srcmd5 in any of our underlay
    # projects
    def try_to_find_left_packages(self, packages):
        for package in packages:
            root = ET.fromstring(self._get_source_package(self.from_prj, package, None))
            linked = root.find('linked')
            if not linked is None and linked.get('package') != package:
                logging.warn("link mismatch: %s <> %s, subpackage?", linked.get('package'), package)
                continue

            logging.debug("check where %s came from", package)
            foundit = False
            for project in self.project_preference_order:
                srcmd5 = self.check_source_in_project(project, package, root.get('verifymd5'))
                if srcmd5:
                    logging.debug('%s -> %s', package, project)
                    self.link_packages(project, package, srcmd5, self.project_mapping[project], package)
                    foundit = True
                    break
            if not foundit:
                logging.debug('%s is a fork', package)

    def get_link(self, project, package):
        try:
            link = self.cached_GET(makeurl(self.apiurl,
                                    ['source', project, package, '_link']))
        except urllib2.HTTPError:
            return None
        return ET.fromstring(link)

    def detach_branch(self, project, package):
        # copy to itself with expand
        url = makeurl(self.apiurl, ['source', project, package], {
            'opackage': package,
            'oproject': project, 'cmd': 'copy', 'expand': '1'}
        )
        try:
            http_POST(url)
        except urllib2.HTTPError, err:
            pass

    def freeze_link(self, project, package):
        link = self.get_link(project, package)
        if link is None:
            return None

        if link.get('baserev'): # bad sign ahead!
            self.detach_branch(project, package)
            # remove targetpkg in all subprojects where it existed before
            for sproject in self.subprojects:
                if package in self.packages[sproject]:
                    self.remove_packages(sproject, [package] )

            return None

        rev = link.get('rev')
        # XXX: magic number?
        if rev and len(rev) > 5:
            return True
        lpackage = link.get('package') or package
        if not link.get('project'):
            if lpackage not in self.packages[project]:
                return False
            return True
        opts = { 'view': 'info' }
        if rev:
            opts['rev'] = rev
        root = self.cached_GET(makeurl(self.apiurl,
                                ['source', link.get('project'), lpackage], opts ))
        root = ET.fromstring(root)
        self.link_packages(link.get('project'), lpackage, root.get('srcmd5'), project, package)
        return True

    def cleanup_and_freeze_links(self, prj, packages):
        logging.debug("check for links to freeze in %s", prj)
        for package in packages:
            try:
                if self.freeze_link(prj, package) == False:
                    logging.error('invalid link %s/%s', prj, package)
                    self.remove_packages(prj, [package])
            except urllib2.HTTPError, e:
                logging.error("failed to freeze {}/{}: {}".format(prj, package, e))


    def check_dups(self):
        """ walk through projects in order of preference and warn about
        duplicates in overlayed projects"""
        mypackages = dict()
        for project in self.subprojects:
            for package in self.packages[project]:
                if package in mypackages:
                    logging.warn("duplicate %s/%s, in %s", project, package, mypackages[package])
                else:
                    mypackages[package] = project

    def freeze_candidates(self):
        url = makeurl(self.apiurl, ['source', 'openSUSE:Factory'], { 'view': 'info' } )
        root = ET.fromstring(self.cached_GET(url))
        flink = ET.Element('frozenlinks')
        fl = ET.SubElement(flink, 'frozenlink', {'project': 'openSUSE:Factory'})

        for package in root.findall('sourceinfo'):
            exists = False
            if package.get('package').startswith('_product'):
                continue
            for prj in self.projects:
                if package.get('package') in self.packages[prj]:
                    exists = True
            if exists:
                continue
            ET.SubElement(fl, 'package', { 'name': package.get('package'),
                                           'srcmd5': package.get('srcmd5'),
                                           'vrev': package.get('vrev') })

        url = makeurl(self.apiurl, ['source', '%s:Factory-Candidates-Check'%self.from_prj, '_project', '_frozenlinks'], {'meta': '1'})
        try:
            http_PUT(url, data=ET.tostring(flink))
        except urllib2.HTTPError, err:
            logging.error(err)

    def get_specfiles(self, project, package):
        url = makeurl(self.apiurl, ['source', project, package], { 'expand': '1' } )
        root = None
        while root is None:
            try:
                root = ET.fromstring(self.cached_GET(url))
            except urllib2.HTTPError as e:
                print "ERR", e.reason
                # this can happen if the build service didn't yet work on the previous commit
                if e.code == 400 and e.reason == 'service in progress':
                    time.sleep(1)
                    continue
                raise
        files = [ entry.get('name').replace('.spec', '') for entry in root.findall('entry') if entry.get('name').endswith('.spec') ]
        if len(files) == 1:
            return None, files[0]
        mainpackage = None
        for subpackage in files[:]:
            link = self.get_link(project, subpackage)
            if link is not None:
                if link.get('project') and link.get('project') != project:
                    mainpackage = subpackage
                    files.remove(subpackage)

        logging.info("%s/%s subpackages: %s [%s]", project, package, mainpackage, ','.join(files))

        return files, mainpackage

def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = UpdateCrawler(args.from_prj, caching = args.cache_requests )
    uc.check_dups()
    uc.crawl(args.package)

    if args.no_update_candidates == False:
        uc.freeze_candidates()

if __name__ == '__main__':
    description = 'maintain sort openSUSE:42 packages into subprojects'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('--skip-sanity-checks', action='store_true',
                        help='don\'t do slow check for broken links (only for testing)')
    parser.add_argument('-n', '--dry', action='store_true',
                        help='dry run, no POST, PUT, DELETE')
    parser.add_argument('--cache-requests', action='store_true', default=False,
                        help='cache GET requests. Not recommended for daily use.')
    parser.add_argument('--no-update-candidates', action='store_true',
                        help='don\'t update Factory candidates project')
    parser.add_argument("package", nargs='*', help="package to check")

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    if args.dry:
        def dryrun(t, *args, **kwargs):
            return lambda *args, **kwargs: logging.debug("dryrun %s %s %s", t, args, str(kwargs)[:30])

        http_POST = dryrun('POST')
        http_PUT = dryrun('PUT')
        http_DELETE = dryrun('DELETE')

    sys.exit(main(args))

# vim:sw=4 et
