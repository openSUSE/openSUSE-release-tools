#!/usr/bin/python3

import argparse
import logging
import sys
import os
import shutil
import git

from lxml import etree as ET

import osc.conf
from osclib.core import source_file_ensure
from osclib.core import project_pseudometa_package

META_FILE = 'SLFO_Packagelist.group'


class PackagelistUploader(object):
    def __init__(self, project, print_only, verbose):
        self.project = project
        self.print_only = print_only
        self.verbose = verbose
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']

    def create_packagelist(self, packages=[]):
        packagelist = ET.Element('packagelist')
        for pkg in sorted(packages):
            if not self.print_only and self.verbose:
                print(pkg)
            attr = {'name': pkg}
            ET.SubElement(packagelist, 'package', attr)

        return ET.tostring(packagelist, pretty_print=True, encoding='unicode')

    def crawl(self):
        """Main method"""
        cwd = os.getcwd()
        filepath = os.path.join(cwd, 'SLFO_main')
        if os.path.isdir(filepath):
            shutil.rmtree(filepath)
        gitpath = 'https://src.opensuse.org/products/SLFO_main.git'
        git.Repo.clone_from(gitpath, filepath, branch='main')
        packages = []
        files = os.listdir(filepath)
        for f in files:
            if f.startswith("."):
                continue
            fullpath = os.path.join(filepath, f)
            if os.path.isdir(fullpath):
                packages.append(f)

        slfo_packagelist = self.create_packagelist(packages)
        pseudometa_project, pseudometa_package = project_pseudometa_package(self.apiurl, self.project)
        if not self.print_only:
            source_file_ensure(self.apiurl, pseudometa_project, pseudometa_package, META_FILE,
                               slfo_packagelist, 'Update SLFO packagelist')
        else:
            print(slfo_packagelist)


def main(args):
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    if args.project is None:
        print("Please pass --project argument. See usage with --help.")
        quit()

    uc = PackagelistUploader(args.project, args.print_only, args.verbose)
    uc.crawl()


if __name__ == '__main__':
    description = 'Upload SLFO packagelist from src.opensuse.org to staging meta package.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-p', '--project', dest='project', metavar='PROJECT',
                        help='Target project on buildservice')
    parser.add_argument('-o', '--print-only', action='store_true',
                        help='show the result instead of the uploading')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='show the diff')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
