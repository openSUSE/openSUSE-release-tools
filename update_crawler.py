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
import urllib2
from xml.etree import cElementTree as ET

import osc.conf
import osc.core
import rpm

from osclib.memoize import memoize

OPENSUSE = 'openSUSE:42'
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

    @memoize()
    def _get_source_infos(self, project):
        return http_GET(makeurl(self.apiurl,
                                ['source', project],
                                {
                                    'view': 'info'
                                })).read()

    def get_source_infos(self, project):
        root = ET.fromstring(self._get_source_infos(project))
        ret = dict()
        for package in root.findall('sourceinfo'):
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
        res = 0
        if not reqs:
            print "creating submit request", src_project, src_package, rev, dst_project, dst_package
            #return 0
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

    def split_packages(self, packages):
        filtered_sle = dict()
        filtered_fac = dict()
        for package, sourceinfo in packages.items():
            # directly in 42
            if sourceinfo.find('originproject') is None:
                continue
            if sourceinfo.find('originproject').text == 'openSUSE:42:SLE12-Picks':
                filtered_sle[package] = sourceinfo
            elif sourceinfo.find('originproject').text == 'openSUSE:42:Factory-Copies':
                filtered_fac[package] = sourceinfo
        return filtered_sle, filtered_fac

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

    def update_targets(self, targets, sources, default_origin):
        for package, sourceinfo in targets.items():
            if not package in sources:
                logging.info('FATAL: Package %s not found in targets' % (package))
                continue

            source = sources[package]

            #if package != 'build-compare':
            #    continue
            
            # Compare verifymd5
            md5_from = source.get('verifymd5')
            md5_to = sourceinfo.get('verifymd5')
            if md5_from == md5_to:
                #logging.info('Package %s not marked for update' % package)
                continue

            if self.is_source_innerlink(OPENSUSE, package):
                logging.info('Package %s is sub package' % (package))
                continue

            originproject = default_origin
            if not source.find('originproject') is None:
                originproject = source.find('originproject').text

            src_project, src_package, src_rev = self.follow_link(originproject, package,
                                                                 source.get('srcmd5'), source.get('verifymd5'))

            res = self.submitrequest(src_project, src_package, src_rev, package)
            if res:
                logging.info('Created request %s for %s' % (res, package))
            elif res != 0:
                logging.error('Error creating the request for %s' % package)


    def crawl(self):
        """Main method of the class that run the crawler."""
        targets_sle, targets_fac = self.split_packages(self.get_source_infos(OPENSUSE))
        self.update_targets(targets_sle, self.get_source_infos(SLE), SLE)
        self.update_targets(targets_fac, self.get_source_infos(FACTORY), FACTORY)


def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = UpdateCrawler(SLE, OPENSUSE)
    uc.crawl()

if __name__ == '__main__':
    description = 'Create SR from SLE to the new openSUSE:42 project for '\
                  'every new update.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
