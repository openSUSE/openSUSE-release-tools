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
SLE = 'SUSE:SLE-12:Update'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET


class UpdateCrawler(object):
    def __init__(self, from_prj, to_prj):
        self.from_prj = from_prj
        self.to_prj = to_prj
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']

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
    def _get_source_package(self, project, package):
        return http_GET(makeurl(self.apiurl,
                                ['source', project, package],
                                {
                                    'view': 'info',
                                    'parse': 1,
                                })).read()

    def get_source_verifymd5(self, project, package):
        """ Return the verifymd5 of a source package."""
        root = ET.fromstring(self._get_source_package(project, package))
        return root.get('verifymd5')

    def get_source_version(self, project, package):
        """ Return the version of a source package."""
        root = ET.fromstring(self._get_source_package(project, package))
        epoch = '0'
        version = root.find('version').text
        release = root.find('release').text
        return (epoch, version, release)

    def get_update_candidates(self):
        """Get the grouped update list from `fron_prj` project.

        Return a list of updates for every package, including only the
        last update for every package. Every element is a tuple, where
        the first element is the name of the package and the second
        one the most update version of the package:
        [
          ('DirectFB', 'DirectFB.577'),
          ('MozillaFirefox', 'MozillaFirefox.544'),
          ('PackageKit', 'PackageKit.103'),
          ...,
        ]

        """
        # From the list of packages, filter non-updates and the
        # 'patchinfo'
        packages = self.get_source_packages(self.from_prj)
        packages = [i for i in packages
                    if not i.startswith('patchinfo') and i.split('.')[-1].isdigit()]
        # Group by package name and revert the order of updates
        updates = [list(reversed(list(i)))
                   for _, i in itertools.groupby(packages,
                                                 lambda x: x.split('.')[0])]
        # Get the last version of every package
        updates = [(i[0].split('.')[0], i[0]) for i in updates]
        return updates

    def _submitrequest(self, src_project, src_package, dst_project,
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
            res = osc.core.create_submit_request(self.apiurl,
                                                 src_project,
                                                 src_package,
                                                 dst_project,
                                                 dst_package,
                                                 message=msg)
        return res

    def submitrequest(self, src_package, dst_package):
        """Create a submit request using the osc.commandline.Osc class."""
        src_project = self.from_prj
        dst_project = self.to_prj
        msg = 'Automatic request from %s by UpdateCrawler' % src_project
        return self._submitrequest(src_project, src_package, dst_project,
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

    def crawl(self):
        """Main method of the class that run the crawler."""
        updates = self.get_update_candidates()

        packages = set(self.get_source_packages(self.to_prj, expand=True))
        to_update = []
        for package, update in updates:
            if package not in packages:
                logging.info('Package %s not found in %s' % (package, self.to_prj))
                continue

            # Compare version
            version_from = self.get_source_version(self.from_prj, update)
            version_to = self.get_source_version(self.to_prj, package)
            if rpm.labelCompare(version_to, version_from) > 0:
                logging.info('Package %s with version %s found in %s with '
                             'version %s. Ignoring the package '
                             '(comes from Factory?)' % (package, version_from,
                                                        self.to_prj, version_to))
                continue

            # Compare verifymd5
            md5_from = self.get_source_verifymd5(self.from_prj, update)
            md5_to = self.get_source_verifymd5(self.to_prj, package)
            if md5_from == md5_to:
                logging.info('Package %s not marked for update' % package)
                continue

            if self.is_source_innerlink(self.from_prj, update):
                logging.info('Package %s sub-spec file' % package)
                continue

            # Mark the package for an update
            to_update.append((package, update))
            logging.info('Package %s marked for update' % package)

        for package, update in to_update:
            res = self.submitrequest(update, package)
            if res:
                logging.info('Created request %s for %s' % (res, package))
            elif res != 0:
                logging.error('Error creating the request for %s' % package)


def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = UpdateCrawler(args.from_prj, args.to_prj)
    uc.crawl()

if __name__ == '__main__':
    description = 'Create SR from SLE to the new openSUSE:42 project for '\
                  'every new update.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % SLE,
                        default=SLE)
    parser.add_argument('-t', '--to', dest='to_prj', metavar='PROJECT',
                        help='project where to submit the updates (default: %s)' % OPENSUSE,
                        default=OPENSUSE)

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
