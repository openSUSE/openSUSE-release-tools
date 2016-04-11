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
import yaml

from osclib.memoize import memoize

OPENSUSE = 'openSUSE:Leap:42.2'

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

        self.parse_lookup()
        self.fill_package_meta()
        self.packages = dict()
        for project in [self.from_prj] + self.project_preference_order:
            self.packages[project] = self.get_source_packages(project)

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
            
    def parse_lookup(self):
        self.lookup = yaml.safe_load(self._load_lookup_file())
        self.lookup_changes = 0
        
    def _load_lookup_file(self):
        return self.cached_GET(makeurl(self.apiurl,
                                       ['source', self.from_prj, '00Meta', 'lookup.yml']))

    def _put_lookup_file(self, data):
        return http_PUT(makeurl(self.apiurl,
                                ['source', self.from_prj, '00Meta', 'lookup.yml']), data=data)

    def store_lookup(self):
        if self.lookup_changes == 0:
            logging.info('no change to lookup.yml')
            return
        data = yaml.dump(self.lookup, default_flow_style=False, explicit_start=True)
        self._put_lookup_file(data)
        self.lookup_changes = 0
    
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
                logging.warn('Retrying {}'.format(url))
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


    def crawl(self, given_packages = None):
        """Main method of the class that run the crawler."""

        self.try_to_find_left_packages(given_packages or self.packages[self.from_prj])
        self.store_lookup()

    def get_package_history(self, project, package, deleted = False):
        try:
            query = {}
            if deleted:
                query['deleted'] = 1
            return self.cached_GET(makeurl(self.apiurl,
                                   ['source', project, package, '_history'], query))
        except urllib2.HTTPError, e:
            if e.code == 404:
                return None
            raise
        
    def check_source_in_project(self, project, package, verifymd5, deleted=False):
        if project not in self.packages:
            self.packages[project] = self.get_source_packages(project)

        if not deleted and not package in self.packages[project]:
            return None, None

        his = self.get_package_history(project, package, deleted)
        if his is None:
            return None, None

        his = ET.fromstring(his)
        historyrevs = dict()
        revs = list()
        for rev in his.findall('revision'):
            historyrevs[rev.find('srcmd5').text] = rev.get('rev')
            revs.append(rev.find('srcmd5').text)
        revs.reverse()
        for i in range(min(len(revs), 5)): # check last commits
            srcmd5=revs.pop(0)
            root = self.cached_GET(makeurl(self.apiurl,
                                    ['source', project, package], { 'rev': srcmd5, 'view': 'info'}))
            root = ET.fromstring(root)
            if root.get('verifymd5') == verifymd5:
                return srcmd5, historyrevs[srcmd5]
        return None, None

    # check if we can find the srcmd5 in any of our underlay
    # projects
    def try_to_find_left_packages(self, packages):
        for package in sorted(packages):

            lproject = self.lookup.get(package, None)
            if not package in self.packages[self.from_prj]:
                logging.info("{} vanished".format(package))
                if self.lookup.get(package):
                    del self.lookup[package]
                    self.lookup_changes += 1
                continue

            root = ET.fromstring(self._get_source_package(self.from_prj, package, None))
            pm = self.package_metas[package]
            devel = pm.find('devel')
            if devel is not None or lproject.startswith('Devel;'):
                develprj = None
                develpkg = None
                if devel is None:
                    (dummy, develprj, develpkg) = lproject.split(';')
                    logging.warn('{} lacks devel project setting {}/{}'.format(package, develprj, develpkg))
                else:
                    develprj = devel.get('project')
                    develpkg = devel.get('package')
                srcmd5, rev = self.check_source_in_project(develprj, develpkg,
                                                           root.get('verifymd5'))
                if srcmd5:
                    lstring = 'Devel;{};{}'.format(develprj, develpkg)
                    if lstring != self.lookup[package]:
                        logging.debug("{} from devel {}/{} (was {})".format(package, develprj, develpkg, lproject))
                        self.lookup[package] = lstring
                        self.lookup_changes += 1
                    else:
                        logging.debug("{} lookup from {}/{} is correct".format(package, develprj, develpkg))
                    continue

            linked = root.find('linked')
            if not linked is None and linked.get('package') != package:
                lstring = 'subpackage of {}'.format(linked.get('package'))
                if lstring != lproject:
                    logging.warn("link mismatch: %s <> %s, subpackage? (was {})", linked.get('package'), package, lproject)
                    self.lookup[package] = lstring
                    self.lookup_changes += 1
                else:
                    logging.debug("{} correctly marked as subpackage of {}".format(package, linked.get('package')))
                continue
            
            if lproject and lproject != 'FORK':
                srcmd5, rev = self.check_source_in_project(lproject, package, root.get('verifymd5'))
                if srcmd5:
                    logging.debug("{} lookup from {} is correct".format(package, lproject))
                    continue
                if lproject == 'openSUSE:Factory':
                    his = self.get_package_history(lproject, package, deleted=True)
                    if his:
                        logging.debug("{} got dropped from {}".format(package, lproject))
                        continue
            
            logging.debug("check where %s came from", package)
            foundit = False
            for project in self.project_preference_order:
                srcmd5, rev = self.check_source_in_project(project, package, root.get('verifymd5'))
                if srcmd5:
                    logging.info('{} -> {} (was {})'.format(package, project, lproject))
                    self.lookup[package] = project
                    self.lookup_changes += 1
                    foundit = True
                    break

            if not foundit:
                if lproject == 'FORK':
                    logging.debug("{}: lookup is correctly marked as fork".format(package))
                else:
                    logging.info('{} is a fork (was {})'.format(package, lproject))
                    self.lookup[package] = 'FORK'
                    self.lookup_changes += 1

            # avoid loosing too much work
            if self.lookup_changes > 50:
                self.store_lookup()
                
    def get_link(self, project, package):
        try:
            link = self.cached_GET(makeurl(self.apiurl,
                                    ['source', project, package, '_link']))
        except urllib2.HTTPError:
            return None
        return ET.fromstring(link)

    def fill_package_meta(self):
        self.package_metas = dict()
        url = makeurl(self.apiurl, ['search', 'package'], "match=[@project='%s']" % self.from_prj)
        root = ET.fromstring(self.cached_GET(url))
        for p in root.findall('package'):
            name = p.attrib['name']
            self.package_metas[name] = p


def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = UpdateCrawler(args.from_prj, caching = args.cache_requests )
    given_packages = args.packages
    if not args.all and not given_packages:
        given_packages = uc.latest_packages()
    uc.crawl(given_packages)

if __name__ == '__main__':
    description = 'maintain %s/00Meta/lookup.yml' % OPENSUSE
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-a', '--all', action='store_true',
                        help='check all packages')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('-n', '--dry', action='store_true',
                        help='dry run, no POST, PUT, DELETE')
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

# vim:sw=4 et
