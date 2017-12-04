#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2017 SUSE Linux GmbH
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
import logging
import sys
import urllib2
import re
from xml.etree import cElementTree as ET

import osc.conf
import osc.core

from osc import oscerr

OPENSUSE = 'openSUSE:Leap:15.0'
SLE = 'SUSE:SLE-15:GA'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_POST = osc.core.http_POST

class CompareList(object):
    def __init__(self, old_prj, new_prj, verbose, newonly, removedonly, existin, submit, submitfrom, submitto):
        self.new_prj = new_prj
        self.old_prj = old_prj
        self.verbose = verbose
        self.newonly = newonly
        self.existin = existin
        self.submit = submit
        self.submitfrom = submitfrom
        self.submitto = submitto
        self.removedonly = removedonly
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']

    def get_source_packages(self, project):
        """Return the list of packages in a project."""
        query = {'expand': 1}
        root = ET.parse(http_GET(makeurl(self.apiurl, ['source', project],
                                 query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]

        return packages

    def item_exists(self, project, package=None):
        """
        Return true if the given project or package exists
        """
        if package:
            url = makeurl(self.apiurl, ['source', project, package, '_meta'])
        else:
            url = makeurl(self.apiurl, ['source', project, '_meta'])
        try:
            http_GET(url)
        except urllib2.HTTPError:
            return False
        return True

    def removed_pkglist(self, project):
        if project.startswith('SUSE:'):
            apiurl = 'https://api.suse.de'
        else:
            apiurl = self.apiurl
        query = "match=state/@name='accepted'+and+(action/target/@project='{}'+and+action/@type='delete')".format(project)
        url = makeurl(apiurl, ['search', 'request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        packages = [t.get('package') for t in root.findall('./request/action/target')]

        return packages

    def is_linked_package(self, project, package):
        u = makeurl(self.apiurl, ['source', project, package])
        root = ET.parse(http_GET(u)).getroot()
        linked = root.find('linkinfo')
        return linked

    def check_diff(self, package, old_prj, new_prj):
        logging.debug('checking %s ...' % package)
        query = {'cmd': 'diff',
                 'view': 'xml',
                 'oproject': old_prj,
                 'opackage': package}
        u = makeurl(self.apiurl, ['source', new_prj, package], query=query)
        root = ET.parse(http_POST(u)).getroot()
        old_srcmd5 = root.findall('old')[0].get('srcmd5')
        logging.debug('%s old srcmd5 %s in %s' % (package, old_srcmd5, old_prj))
        new_srcmd5 = root.findall('new')[0].get('srcmd5')
        logging.debug('%s new srcmd5 %s in %s' % (package, new_srcmd5, new_prj))
        # Compare srcmd5
        if old_srcmd5 != new_srcmd5:
            # check if it has diff element
            diffs = root.findall('files/file/diff')
            if diffs:
                return ET.tostring(root)
        return False

    def submit_new_package(self, source, target, package):
        req = osc.core.get_request_list(self.apiurl, target, package, req_state=('new', 'review', 'declined'))
        if req:
            print("There is a request to %s / %s already, skip!"%(target, package))
        else:
            msg = 'New package submitted by compare_pkglist'
            res = osc.core.create_submit_request(self.apiurl, source, package, target, package, message=msg)
            if res and res is not None:
                print('Created request %s for %s' % (res, package))
            else:
                print('Error occurred when creating the submit request')

    def crawl(self):
        """Main method"""
        if self.submit:
            if (self.submitfrom and not self.submitto) or (self.submitto and not self.submitfrom):
                print("** Please give both --submitfrom and --submitto parameter **")
                return
            if self.submitfrom and self.submitto:
                if not self.item_exists(self.submitfrom):
                    print("Project %s is not exist"%self.submitfrom)
                    return
                if not self.item_exists(self.submito):
                    print("Project %s is not exist"%self.submitto)
                    return

        # get souce packages from target
        print 'Gathering the package list from %s' % self.old_prj
        source = self.get_source_packages(self.old_prj)
        print 'Gathering the package list from %s' % self.new_prj
        target = self.get_source_packages(self.new_prj)
        removed_packages = self.removed_pkglist(self.old_prj)
        if self.existin:
            print 'Gathering the package list from %s' % self.existin
            existin_packages = self.get_source_packages(self.existin)

        if not self.removedonly:
            for pkg in source:
                if pkg.startswith('000') or pkg.startswith('_'):
                    continue

                if pkg not in target:
                    # ignore the second specfile package
                    linked = self.is_linked_package(self.old_prj, pkg)
                    if linked is not None:
                        continue

                    if self.existin:
                        if pkg not in existin_packages:
                            continue

                    print("New package than {:<8} - {}".format(self.new_prj, pkg))
                    if self.submit:
                        if self.submitfrom and self.submitto:
                            if not self.item_exists(self.submitfrom, pkg):
                                print("%s not found in %s"%(pkg, self.submitfrom))
                                continue
                            self.submit_new_package(self.submitfrom, self.submitto, pkg)
                        else:
                            self.submit_new_package(self.old_prj, self.new_prj, pkg)
                elif not self.newonly:
                    diff = self.check_diff(pkg, self.old_prj, self.new_prj)
                    if diff:
                        print("Different source in {:<8} - {}".format(self.new_prj, pkg))
                        if self.verbose:
                            print("=== Diff ===\n{}".format(diff))

        for pkg in removed_packages:
            if pkg in target:
                print("Deleted package in {:<8} - {}".format(self.old_prj, pkg))

def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = CompareList(args.old_prj, args.new_prj, args.verbose, args.newonly,
            args.removedonly, args.existin, args.submit, args.submitfrom, args.submitto)
    uc.crawl()

if __name__ == '__main__':
    description = 'Compare packages status between two project'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-o', '--old', dest='old_prj', metavar='PROJECT',
                        help='the old project where to compare (default: %s)' % SLE,
                        default=SLE)
    parser.add_argument('-n', '--new', dest='new_prj', metavar='PROJECT',
                        help='the new project where to compare (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='show the diff')
    parser.add_argument('--newonly', action='store_true',
                        help='show new package only')
    parser.add_argument('--removedonly', action='store_true',
                        help='show removed package but exists in target')
    parser.add_argument('--existin', dest='existin', metavar='PROJECT',
                        help='the package exists in the project')
    parser.add_argument('--submit', action='store_true', default=False,
                        help='submit new package to target, FROM and TO can re-configureable by --submitfrom and --submitto')
    parser.add_argument('--submitfrom', dest='submitfrom', metavar='PROJECT',
                        help='submit new package from, define --submitto is required')
    parser.add_argument('--submitto', dest='submitto', metavar='PROJECT',
                        help='submit new package to, define --submitfrom is required')

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
