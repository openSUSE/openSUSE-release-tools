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

logger = logging.getLogger()

OPENSUSE = 'openSUSE:Leap:42.3'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_DELETE = osc.core.http_DELETE
http_PUT = osc.core.http_PUT
http_POST = osc.core.http_POST

class Manager42(object):
    def __init__(self, from_prj, caching = True, apiurl_default = None):
        self.from_prj = from_prj
        self.caching = caching
        self._apiurl = osc.conf.config['apiurl']
        self._apiurl_default = apiurl_default
        self._apiurl_multi = apiurl_default is not None and self._apiurl != apiurl_default
        self.projects = None
        self.ibs = self._apiurl.startswith("https://api.suse.de")
        self.project_preference_order = [
                'SUSE:SLE-12-SP3:GA',
                'SUSE:SLE-12-SP2:Update',
                'SUSE:SLE-12-SP2:GA',
                'SUSE:SLE-12-SP1:Update',
                'SUSE:SLE-12-SP1:GA',
                'SUSE:SLE-12:Update',
                'SUSE:SLE-12:GA',
                'openSUSE:Leap:42.2:Update',
                'openSUSE:Leap:42.2',
                'openSUSE:Leap:42.2:NonFree:Update',
                'openSUSE:Leap:42.2:NonFree',
                'openSUSE:Leap:42.1:Update',
                'openSUSE:Leap:42.1',
                'openSUSE:Leap:42.1:NonFree:Update',
                'openSUSE:Leap:42.1:NonFree',
                'openSUSE:Factory',
                'openSUSE:Factory:NonFree',
                'openSUSE:Leap:42.3:SLE-workarounds',
                'openSUSE:Leap:42.2:SLE-workarounds',
                ]

        self.parse_lookup(self.from_prj)
        self.fill_package_meta()
        self.packages = dict()
        for project in [self.from_prj] + self.project_preference_order:
            self.packages[project] = self.get_source_packages(project)

    def apiurl(self, project = None):
        # Use base URL except when evaluating against multiple API endpoints and
        # the project context is available. If the project is not  available on
        # the base endpoint then switch to the default endpoint which is assumed
        # to contain the project. This could be extended to handle a list of API
        # endpoints by tracking the projects of each.
        if self._apiurl_multi:
            if self.projects is None:
                logger.debug("requesting project list")
                self.projects = osc.core.meta_get_project_list(self._apiurl)

            if project is not None and project not in self.projects:
                logger.debug("apiurl = default ({}); project = {}".format(self._apiurl_default, project))
                return self._apiurl_default

            # Only print debug message if in multi mode.
            logger.debug("apiurl = base ({}); project = {}".format(self._apiurl, project))

        return self._apiurl

    def latest_packages(self):
        data = self.cached_GET(makeurl(self.apiurl(self.from_prj),
                                       ['project', 'latest_commits', self.from_prj]))
        lc = ET.fromstring(data)
        packages = set()
        for entry in lc.findall('{http://www.w3.org/2005/Atom}entry'):
            title = entry.find('{http://www.w3.org/2005/Atom}title').text
            if title.startswith('In '):
                packages.add(title[3:].split(' ')[0])
        return sorted(packages)

    def parse_lookup(self, project):
        self.lookup_changes = 0
        self.lookup = {}
        try:
            self.lookup = yaml.safe_load(self._load_lookup_file(project))
        except urllib2.HTTPError, e:
            if e.code != 404:
                raise

    def _load_lookup_file(self, prj):
        return self.cached_GET(makeurl(self.apiurl(prj),
                                       ['source', prj, '00Meta', 'lookup.yml']))

    def _put_lookup_file(self, prj, data):
        return http_PUT(makeurl(self.apiurl(prj),
                                ['source', prj, '00Meta', 'lookup.yml']), data=data)

    def store_lookup(self):
        if self.lookup_changes == 0:
            logger.info('no change to lookup.yml')
            return
        data = yaml.dump(self.lookup, default_flow_style=False, explicit_start=True)
        self._put_lookup_file(self.from_prj, data)
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
                logger.warn('Retrying {}'.format(url))
                time.sleep(1)
                return self.retried_GET(url)
            raise e

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        try:
            root = ET.fromstring(
                self.cached_GET(makeurl(self.apiurl(project),
                                 ['source', project],
                                 query=query)))
            packages = [i.get('name') for i in root.findall('entry')]

        except urllib2.HTTPError, e:
            if e.code == 404:
                logger.error("{}: {}".format(project, e))
                packages = []

        return packages

    def _get_source_package(self, project, package, revision):
        opts = { 'view': 'info' }
        if revision:
            opts['rev'] = revision
        return self.cached_GET(makeurl(self.apiurl(project),
                                ['source', project, package], opts))


    def crawl(self, given_packages = None):
        """Main method of the class that runs the crawler."""

        packages = given_packages or self.packages[self.from_prj]

        for package in sorted(packages):
            try:
                self.check_one_package(package)
            except urllib2.HTTPError, e:
                logger.error("Failed to check {}: {}".format(package, e))
                pass

            # avoid loosing too much work
            if self.lookup_changes > 50:
                self.store_lookup()

        if self.lookup_changes:
            self.store_lookup()

    def get_package_history(self, project, package, deleted = False):
        try:
            query = {}
            if deleted:
                query['deleted'] = 1
            return self.cached_GET(makeurl(self.apiurl(project),
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
            root = self.cached_GET(makeurl(self.apiurl(project),
                                    ['source', project, package], { 'rev': srcmd5, 'view': 'info'}))
            root = ET.fromstring(root)
            if root.get('verifymd5') == verifymd5:
                return srcmd5, historyrevs[srcmd5]
        return None, None


    # check if we can find the srcmd5 in any of our underlay
    # projects
    def check_one_package(self, package):
        lproject = self.lookup.get(package, None)
        if not package in self.packages[self.from_prj]:
            logger.info("{} vanished".format(package))
            if self.lookup.get(package):
                del self.lookup[package]
                self.lookup_changes += 1
            return

        root = ET.fromstring(self._get_source_package(self.from_prj, package, None))
        linked = root.find('linked')
        if not linked is None and linked.get('package') != package:
            lstring = 'subpackage of {}'.format(linked.get('package'))
            if lstring != lproject:
                logger.warn("{} links to {} (was {})".format(package, linked.get('package'), lproject))
                self.lookup[package] = lstring
                self.lookup_changes += 1
            else:
                logger.debug("{} correctly marked as subpackage of {}".format(package, linked.get('package')))
            return

        pm = self.package_metas[package]
        devel = pm.find('devel')
        if devel is not None or (lproject is not None and lproject.startswith('Devel;')):
            develprj = None
            develpkg = None
            if devel is None:
                (dummy, develprj, develpkg) = lproject.split(';')
                logger.warn('{} lacks devel project setting {}/{}'.format(package, develprj, develpkg))
            else:
                develprj = devel.get('project')
                develpkg = devel.get('package')
            srcmd5, rev = self.check_source_in_project(develprj, develpkg,
                                                       root.get('verifymd5'))
            if srcmd5:
                lstring = 'Devel;{};{}'.format(develprj, develpkg)
                if not package in self.lookup or lstring != self.lookup[package]:
                    logger.debug("{} from devel {}/{} (was {})".format(package, develprj, develpkg, lproject))
                    self.lookup[package] = lstring
                    self.lookup_changes += 1
                else:
                    logger.debug("{} lookup from {}/{} is correct".format(package, develprj, develpkg))
                return

        if lproject and lproject != 'FORK' and not lproject.startswith('subpackage '):
            srcmd5, rev = self.check_source_in_project(lproject, package, root.get('verifymd5'))
            if srcmd5:
                logger.debug("{} lookup from {} is correct".format(package, lproject))
                # if it's from Factory we check if the package can be found elsewhere meanwhile
                if lproject != 'openSUSE:Factory':
                    return
            elif lproject == 'openSUSE:Factory' and not package in self.packages[lproject]:
                his = self.get_package_history(lproject, package, deleted=True)
                if his:
                    logger.debug("{} got dropped from {}".format(package, lproject))

        logger.debug("check where %s came from", package)
        foundit = False
        for project in self.project_preference_order:
            srcmd5, rev = self.check_source_in_project(project, package, root.get('verifymd5'))
            if srcmd5:
                if project != lproject:
                    if project.endswith(':SLE-workarounds'):
                        logger.info('{} is from {} but should come from {}'.format(package, project, lproject))
                    else:
                        logger.info('{} -> {} (was {})'.format(package, project, lproject))
                        self.lookup[package] = project
                        self.lookup_changes += 1
                else:
                    logger.debug('{} still coming from {}'.format(package, project))
                foundit = True
                break

        if not foundit:
            if lproject == 'FORK':
                logger.debug("{}: lookup is correctly marked as fork".format(package))
            else:
                logger.info('{} is a fork (was {})'.format(package, lproject))
                self.lookup[package] = 'FORK'
                self.lookup_changes += 1

    def get_link(self, project, package):
        try:
            link = self.cached_GET(makeurl(self.apiurl(project),
                                    ['source', project, package, '_link']))
        except urllib2.HTTPError:
            return None
        return ET.fromstring(link)

    def fill_package_meta(self):
        self.package_metas = dict()
        url = makeurl(self.apiurl(self.from_prj), ['search', 'package'], "match=[@project='%s']" % self.from_prj)
        root = ET.fromstring(self.cached_GET(url))
        for p in root.findall('package'):
            name = p.attrib['name']
            self.package_metas[name] = p


def main(args):
    # Configure OSC
    # Store the default apiurl in addition to the overriden url if the
    # option was set and thus overrides the default config value.
    if args.apiurl is not None:
        osc.conf.get_config()
        apiurl_default = osc.conf.config['apiurl']
    else:
        apiurl_default = None
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = Manager42(args.from_prj, caching = args.cache_requests, apiurl_default = apiurl_default)
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
                        else logging.INFO,
                        format='%(asctime)s - %(module)s:%(lineno)d - %(levelname)s - %(message)s')

    if args.dry:
        def dryrun(t, *args, **kwargs):
            return lambda *args, **kwargs: logger.debug("dryrun %s %s %s", t, args, str(kwargs)[:200])

        http_POST = dryrun('POST')
        http_PUT = dryrun('PUT')
        http_DELETE = dryrun('DELETE')

    sys.exit(main(args))

# vim:sw=4 et
