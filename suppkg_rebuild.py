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
import yaml
from xml.etree import cElementTree as ET
from collections import defaultdict

import osc.conf
import osc.core

from osc import oscerr
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

OPENSUSE = 'openSUSE:Factory'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_POST = osc.core.http_POST
http_PUT = osc.core.http_PUT

class StagingHelper(object):
    def __init__(self, project):
        self.project = project
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        Config(self.project)
        self.api = StagingAPI(self.apiurl, self.project)

    def load_rebuild_data(self, project, package, filename):
        url = makeurl(self.apiurl, ['source', project, package, '{}?expand=1'.format(filename)])
        try: 
            return http_GET(url)
        except urllib2.HTTPError:
            return None

    def save_rebuild_data(self, project, package, filename, content):
        url = makeurl(self.apiurl, ['source', project, package, filename])
        http_PUT(url + '?comment=support+package+rebuild+update', data=content)

    def rebuild_project(self, project):
        query = {'cmd': 'rebuild'}
        url = makeurl(self.apiurl,['build', project], query=query)
        http_POST(url)

    def get_source_packages(self, project):
        """Return the list of packages in a project."""
        query = {'expand': 1}
        root = ET.parse(http_GET(makeurl(self.apiurl,['source', project],
            query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]

        return packages

    def get_support_package_list(self, project, repository):
        f = osc.core.get_buildconfig(self.apiurl, project, repository).splitlines()
        pkg_list = []
        for line in f:
            if re.match('Preinstall', line) or re.match('VMinstall', line):
                content = line.split(':')
                variables = [x.strip() for x in content[1].split(' ')]
                for var in variables:
                    if var != '' and var not in pkg_list:
                        if var.startswith('!') and var[1:] in pkg_list:
                            pkg_list.remove(var[1:])
                        else:
                            pkg_list.append(var)
        return pkg_list

    def get_project_binarylist(self, project, repository, arch):
        query = {'view': 'binarylist', 'repository': repository, 'arch': arch}
        root = ET.parse(http_GET(makeurl(self.apiurl,['build', project, '_result'],
            query=query))).getroot()
        return root

    def get_package_buildinfo(self, project, repository, arch, package):
        url = makeurl(self.apiurl,['build', project, repository, arch, package, '_buildinfo'])
        root = ET.parse(http_GET(url)).getroot()

        return root

    def get_buildinfo_version(self, project, package):
        buildinfo = self.get_package_buildinfo(project, 'standard', 'x86_64', package)
        versrel = buildinfo.find('versrel')
        version = versrel.split('-')[0]

        return version

    def process_project_binarylist(self, project, repository, arch):
        prj_binarylist = self.get_project_binarylist(project, repository, arch)
        files = {}
        for package in prj_binarylist.findall('./result/binarylist'):
            for binary in package.findall('binary'):
                result = re.match(r'(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm', binary.attrib['filename'])
                if not result:
                    continue
                bname = result.group(1)
                if bname.endswith('-debuginfo') or bname.endswith('-debuginfo-32bit'):
                    continue
                if bname.endswith('-debugsource'):
                    continue
                if bname.startswith('::import::'):
                    continue
                if result.group(4) == 'src':
                    continue
                files[bname] = package.attrib['package']

        return files

    def crawl(self):
        """Main method"""
        rebuild_data = self.load_rebuild_data(self.project + ':Staging', 'dashboard', 'support_pkg_rebuild')
        if rebuild_data is None:
            print "There is no support_pkg_rebuild file!"
            return

        logging.info('Gathering support package list from %s' % self.project)
        support_pkgs = self.get_support_package_list(self.project, 'standard')
        files = self.process_project_binarylist(self.project, 'standard', 'x86_64')
        staging_projects = ["%s:%s"%(self.api.cstaging, p) for p in self.api.get_staging_projects_short()]
        cand_sources = defaultdict(list)
        for stg in staging_projects:
            prj_meta = self.api.get_prj_pseudometa(stg)
            prj_staged_packages = [req['package'] for req in prj_meta['requests']]
            for pkg in support_pkgs:
                if files.get(pkg) and files.get(pkg) in prj_staged_packages:
                    if files.get(pkg) not in cand_sources[stg]:
                        cand_sources[stg].append(files.get(pkg))

        tree = ET.parse(rebuild_data)
        root = tree.getroot()

        logging.info('Checking rebuild data...')

        for stg in root.findall('staging'):
            rebuild = stg.find('rebuild').text
            suppkg_list = stg.find('supportpkg').text
            need_rebuild = False
            suppkgs = []
            if suppkg_list:
                suppkgs = suppkg_list.split(',')

            stgname =  stg.get('name')
            if len(cand_sources[stgname]) and rebuild == 'unknown':
                need_rebuild = True
                stg.find('rebuild').text = 'needed'
                new_suppkg_list = ','.join(cand_sources[stgname])
                stg.find('supportpkg').text = new_suppkg_list
            elif len(cand_sources[stgname]) and rebuild != 'unknown':
                for cand in cand_sources[stgname]:
                    if cand not in suppkgs:
                        need_rebuild = True
                        stg.find('rebuild').text = 'needed'
                        break
                new_suppkg_list = ','.join(cand_sources[stgname])
                stg.find('supportpkg').text = new_suppkg_list
            elif not len(cand_sources[stgname]):
                stg.find('rebuild').text = 'unneeded'

            if stg.find('rebuild').text == 'needed':
                need_rebuild = True

            if need_rebuild and not self.api.is_repo_dirty(stgname, 'standard'):
                logging.info('Rebuild %s' % stgname)
                self.rebuild_project(stgname)
                stg.find('rebuild').text = 'unneeded'

        logging.info('Updating support pkg list...')
        logging.debug(ET.tostring(root))
        self.save_rebuild_data(self.project + ':Staging', 'dashboard', 'support_pkg_rebuild', ET.tostring(root))

def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = StagingHelper(args.project)
    uc.crawl()

if __name__ == '__main__':
    description = 'Rebuild project if support package were staged in the staging project'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
            help='print info useful for debuging')
    parser.add_argument('-p', '--project', dest='project', metavar='PROJECT',
            help='deafult project (default: %s)' % OPENSUSE,
            default=OPENSUSE)

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
            else logging.INFO)

    sys.exit(main(args))
