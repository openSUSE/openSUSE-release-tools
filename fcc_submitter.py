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
import logging
import sys
import urllib2
import random
from xml.etree import cElementTree as ET

import osc.conf
import osc.core

from osclib.memoize import memoize

#OPENSUSE = 'openSUSE:42'
# TODO: remove after testing
OPENSUSE = 'home:mlin7442'
FCC = 'openSUSE:42:Factory-Candidates-Check'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET


class FccSubmitter(object):
    def __init__(self, from_prj, to_prj, submit_limit):
        self.from_prj = from_prj
        self.to_prj = to_prj
        self.submit_limit = submit_limit
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        root = ET.parse(http_GET(makeurl(self.apiurl,['source', project],
                                 query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]
        
        return packages

    def get_request_list(self, package):
        return osc.core.get_request_list(self.apiurl, self.to_prj, package, req_state=('new', 'review'))

    def get_build_succeeded_packages(self, project):
        """Get the build succeeded packages from `from_prj` project.
        """

        f = osc.core.show_prj_results_meta(self.apiurl, project)
        root = ET.fromstring(''.join(f))
        #print ET.dump(root)

        pacs = []
        for node in root.findall('result'):
            if node.get('repository') == 'pure_42' and node.get('arch') == 'x86_64':
                for pacnode in node.findall('status'):
                    if pacnode.get('code') == 'succeeded':
                        pacs.append(pacnode.get('package'))
            else:
                logging.error("Can not find pure_42/x86_64 results")

        return pacs

    def create_submitrequest(self, package):
        """Create a submit request using the osc.commandline.Osc class."""
        src_project = self.from_prj
        dst_project = self.to_prj
        msg = 'Automatic request from %s by F-C-C Submitter' % src_project
        res = osc.core.create_submit_request(self.apiurl,
                                             src_project,
                                             package,
                                             dst_project,
                                             package,
                                             message=msg)
        return res

    def crawl(self):
        """Main method"""
        succeeded_packages = []
        succeeded_packages = self.get_build_succeeded_packages(self.from_prj)
        if not len(succeeded_packages) > 0:
            logging.info('No build succeeded package in %s'%self.from_prj)
            return

        # randomize the list
        random.shuffle(succeeded_packages)
        # get souce packages from target
        target_packages = self.get_source_packages(self.to_prj)
        # TODO: remove after testing
        self.submit_limit = 3
        # TODO: check multispec
        for i in range(0, min(self.submit_limit, len(succeeded_packages))):
            package = succeeded_packages[i]
            # make sure the package non-exist in target yet ie. expand=False
            if package not in target_packages:
                # make sure there is no request against same package
                request = self.get_request_list(package)
                if request:
                    logging.debug("There is a request to %s / %s already, skip!"%(package, self.to_prj))
                else:
                    logging.info("%d - Preparing submit %s to %s"%(i, package, self.to_prj))
                    res = self.create_submitrequest(package)
                    if res:
                        logging.info('Created request %s for %s' % (res, package))
                    else:
                        logging.error('Error occurred when creating submit request')
            else:
                logging.debug('%s is exist in %s, skip!'%(package, self.to_prj))



def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = FccSubmitter(args.from_prj, args.to_prj, args.submit_limit)
    uc.crawl()

if __name__ == '__main__':
    description = 'Create SR from openSUSE:42:Factory-Candidates-Check to the '\
                  'new openSUSE:42 project for every new build succeded package.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to check (default: %s)' % FCC,
                        default=FCC)
    parser.add_argument('-t', '--to', dest='to_prj', metavar='PROJECT',
                        help='project where to submit the packages (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('-l', '--limit', dest='submit_limit', metavar='NUMBERS', help='limit numbers packages to submit, default is 100', default=100)

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
