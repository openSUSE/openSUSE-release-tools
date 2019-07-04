#!/usr/bin/python3

import argparse
import logging
import os
import re
import sys

from xml.etree import cElementTree as ET
from urllib.error import HTTPError

import osc.conf
import osc.core

from osclib.conf import Config

OPENSUSE = 'openSUSE:Factory'
PACKAGEFILE = 'packagelist_without_32bitRPMs_imported'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_POST = osc.core.http_POST

class ScanBaselibs(object):
    def __init__(self, project, repository, verbose, wipebinaries):
        self.project = project
        self.verbose = verbose
        self.repo = repository
        self.wipebinaries = wipebinaries
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        Config(self.apiurl, OPENSUSE)
        self.config = osc.conf.config[OPENSUSE]

        # TODO: would be better to parse baselibs.conf to know which arch was blocked
        self.package_whitelist = list(set(self.config.get('allowed-missing-32bit-binaries-importing', '').split(' ')))

    def get_packages(self, project):
        """Return the list of packages in a project."""
        query = {'expand': 1}
        root = ET.parse(http_GET(makeurl(self.apiurl, ['source', project], query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]

        return packages

    def package_has_baselibs(self, project, package):
        query = {'expand': 1}
        root = ET.parse(http_GET(makeurl(self.apiurl, ['source', project, package], query=query))).getroot()
        files = [i.get('name') for i in root.findall('entry') if i.get('name') == 'baselibs.conf']
        if len(files):
            return True
        return False

    def package_has_32bit_binaries(self, project, repo, package):
        query = { 'package' : package,
                  'repository' : repo,
                  'arch' : 'x86_64',
                  'multibuild' : 1,
                  'view' : 'binarylist' }
        root = ET.parse(http_GET(makeurl(self.apiurl, ['build', project, '_result'], query = query))).getroot()
        # assume 32bit importing RPMs can be appeared in multibuild-ed package
        for binarylist in root.findall('./result/binarylist'):
            binaries = [i.get('filename') for i in binarylist.findall('binary') if i.get('filename').startswith('::import::i586::')]
            if len(binaries):
                return True
        return False

    def check_package_baselibs(self, project, repo, wipebinaries):
        """Main method"""
        # get souce packages from target
        if self.verbose:
            print('Gathering the package list from %s' % project)
        packages = self.get_packages(project)

        with open(os.getcwd() + '/' + PACKAGEFILE, "a") as f:
            for pkg in packages:
                if self.package_has_baselibs(project, pkg) and pkg not in self.package_whitelist:
                    if not self.package_has_32bit_binaries(project, repo, pkg):
                        f.write("%s\n" % pkg)
                        if self.verbose:
                            print('%s has baselibs.conf but 32bit RPMs does not exist on 64bit\'s build result.' % pkg)
                        if wipebinaries:
                            http_POST(makeurl(self.apiurl, ['build', project], {
                                'cmd' : 'wipe',
                                'repository' : repo,
                                'package' : pkg,
                                'arch' : 'i586' }))
            f.close()

    def scan(self):
        """Main method"""
        try:
            osc.core.show_project_meta(self.apiurl, self.project)
        except HTTPError as e:
            if e.code == 404:
                print("Project %s does not exist!" % self.project)
                return

        print('Scanning...')
        if os.path.isfile(os.getcwd() + '/' + PACKAGEFILE):
            os.remove(os.getcwd() + '/' + PACKAGEFILE)
        self.check_package_baselibs(self.project, self.repo, self.wipebinaries)
        print('Done')

def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = ScanBaselibs(args.project, args.repository, args.verbose, args.wipebinaries)
    uc.scan()

if __name__ == '__main__':
    description = 'Verifying 32bit binaries has imported properly towards a project, ' \
                  'if the 32bit binaries were not exist then wipes 32bit build result.' \
                  'This script is now only works on x86_64/i586'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print the information useful for debugging')
    parser.add_argument('-p', '--project', dest='project', metavar='PROJECT',
                        help='the project to check (default: %s)' % OPENSUSE,
                        default=OPENSUSE)
    parser.add_argument('-r', '--repository', dest='repository', metavar='REPOSITORY',
                        help='the repository of binaries (default: %s)' % 'standard',
                        default='standard')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='show the verbose information')
    parser.add_argument('-w', '--wipebinaries', action='store_true', default=False,
                        help='wipe binaries found without imported 32bit RPMs')

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
