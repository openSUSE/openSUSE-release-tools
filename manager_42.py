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

from osclib.memoize import memoize

OPENSUSE = 'openSUSE:42'
SLE = 'SUSE:SLE-12:Update'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_DELETE = osc.core.http_DELETE
http_PUT = osc.core.http_PUT
http_POST = osc.core.http_POST


class UpdateCrawler(object):
    def __init__(self, from_prj):
        self.from_prj = from_prj
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        self.project_mapping = {}
        for prj in ['SUSE:SLE-12:Update', 'SUSE:SLE-12:GA']:
            self.project_mapping[prj] = 'openSUSE:42:SLE12-Picks'
        self.project_mapping['openSUSE:Factory'] = 'openSUSE:42:Factory-Copies'

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        root = ET.parse(
            http_GET(makeurl(self.apiurl,
                             ['source', project],
                             query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]
        return packages

    @memoize()
    def _get_source_package(self, project, package, revision):
        opts = { 'view': 'info' }
        if revision:
            opts['rev'] = revision
        return http_GET(makeurl(self.apiurl,
                                ['source', project, package], opts)).read()
    
    def get_latest_request(self, project, package):
        history = http_GET(makeurl(self.apiurl,
                                   ['source', project, package, '_history'])).read()
        root = ET.fromstring(history)
        requestid = None
        # latest commit's request - if latest commit is not a request, ignore the package
        for r in root.findall('revision'):
            requestid = r.find('requestid')
        if requestid is None:
            return None
        return requestid.text

    def get_request_infos(self, requestid):
        request = http_GET(makeurl(self.apiurl,
                                   ['request', requestid])).read()
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
            url = makeurl(self.apiurl, ['source', project, package])
            try:
                http_DELETE(url)
            except urllib2.HTTPError, err:
                if err.code == 404:
                    # not existant package is ok, we delete them all
                    pass
                else:
                    # If the package was there bug could not be delete, raise the error
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
        print "PUT", url
        http_PUT(url, data=dst_meta)

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
        print "PUT", url
        http_PUT(url, data=link_string)

    def link_packages(self, packages, sourceprj, sourcepkg, sourcerev, targetprj, targetpkg):
        print packages, sourceprj, sourcepkg, sourcerev, targetpkg
        self.remove_packages('openSUSE:42:SLE12-Picks', packages)
        self.remove_packages('openSUSE:42:Factory-Copies', packages)
        self.remove_packages('openSUSE:42:SLE-Pkgs-With-Overwrites', packages)

        self.create_package_container(targetprj, targetpkg)
        link = self._link_content(sourceprj, sourcepkg, sourcerev)
        self.upload_link(targetprj, targetpkg, link)

        for package in [ p for p in packages if p != targetpkg ]:
            link = "<link cicount='copy' package='{}' />".format(targetpkg)
            self.create_package_container(targetprj, package)
            self.upload_link(targetprj, package, link)

        self.remove_packages('openSUSE:42', packages)

    def crawl(self):
        """Main method of the class that run the crawler."""

        packages = self.get_source_packages(self.from_prj, expand=False)
        packages = [ p for p in packages if not p.startswith('_') ]
        requests = dict()

        left_packages = []
        
        for package in packages:
            requestid = self.get_latest_request(self.from_prj, package)
            if requestid is None:
                print package, "is not from request"
                left_packages.append(package)
                continue
            if requestid in requests:
                requests[requestid].append(package)
            else:
                requests[requestid] = [package]

        for request, packages in requests.items():
            sourceprj, sourcepkg, sourcerev, targetpkg = self.get_request_infos(request)
            if not sourceprj in self.project_mapping:
                print "source", sourceprj
                left_packages = left_packages + packages
                continue
            print request, packages, sourceprj, sourcepkg, sourcerev, targetpkg
            targetprj = self.project_mapping[sourceprj]
            self.link_packages(packages, sourceprj, sourcepkg, sourcerev, targetprj, targetpkg)

        return left_packages

    def check_factory_sources(self, package, verifymd5):
        try:
            his = http_GET(makeurl(self.apiurl,
                                   ['source', 'openSUSE:Factory', package, '_history'])).read()
        except urllib2.HTTPError:
            return None
        
        his = ET.fromstring(his)
        revs = list()
        for rev in his.findall('revision'):
            revs.append(rev.find('srcmd5').text)
        revs.reverse()
        for i in xrange(5): # check last 5 commits
            srcmd5=revs.pop(0)
            root = http_GET(makeurl(self.apiurl,
                                    ['source', 'openSUSE:Factory', package], { 'rev': srcmd5, 'view': 'info'})).read()
            root = ET.fromstring(root)
            if root.get('verifymd5') == verifymd5:
                return srcmd5
        return None
        
    def try_to_find_left_packages(self, packages):
        for package in packages:
            root = ET.fromstring(self._get_source_package(self.from_prj, package, None))
            linked = root.find('linked')
            if not linked is None and linked.get('package') != package:
                print "subpackage?"
                continue
            srcmd5 = self.check_factory_sources(package, root.get('verifymd5'))
            if srcmd5:
                self.link_packages([ package ], 'openSUSE:Factory', package, srcmd5, self.project_mapping['openSUSE:Factory'], package)

    def check_link(self, project, package):
        link = http_GET(makeurl(self.apiurl,
                                ['source', project, package, '_link'])).read()
        link = ET.fromstring(link)
        rev = link.get('rev')
        if rev and len(rev) > 5:
            return True
        if link.get('cicount'):
            return True
        opts = { 'view': 'info' }
        if rev:
            opts['rev'] = rev
        root = http_GET(makeurl(self.apiurl,
                                ['source', link.get('project'), link.get('package')], opts )).read()
        root = ET.fromstring(root)
        self.link_packages([package], link.get('project'), link.get('package'), root.get('srcmd5'), project, package)
        
    def find_invalid_links(self, prj):
        packages = self.get_source_packages(prj)
        for package in packages:
            self.check_link(prj, package)

    def check_dups(self):
        mypackages = dict()
        for project in ['openSUSE:42', 'openSUSE:42:SLE-Pkgs-With-Overwrites', 'openSUSE:42:Factory-Copies', 'openSUSE:42:SLE12-Picks']:
            for package in self.get_source_packages(project):
                if package in mypackages:
                    # TODO: detach only if actually a link to the deleted package
                    url = makeurl(self.apiurl, ['source', 'openSUSE:42', package], { 'opackage': package, 'oproject': 'openSUSE:42', 'cmd': 'copy', 'expand': '1'} )
                    try:
                        http_POST(url)
                    except urllib2.HTTPError:
                        continue
                    url = makeurl(self.apiurl, ['source', project, package])
                    http_DELETE(url)
                else:
                    mypackages[package] = project
def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = UpdateCrawler(args.from_prj)
    uc.check_dups()
    #lp = uc.crawl()
    #uc.try_to_find_left_packages(lp)
    #uc.find_invalid_links('openSUSE:42:SLE12-Picks')
    uc.find_invalid_links('openSUSE:42:Factory-Copies')
    
    
if __name__ == '__main__':
    description = 'Create SR from SLE to the new openSUSE:42 project for '\
                  'every new update.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % OPENSUSE,
                        default=OPENSUSE)

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
