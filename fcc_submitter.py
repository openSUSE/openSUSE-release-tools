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
import re
from xml.etree import cElementTree as ET

import osc.conf
import osc.core

from osc import oscerr
from osclib.memoize import memoize

OPENSUSE = 'openSUSE:Leap:42.2'
FCC = 'openSUSE:42:Factory-Candidates-Check'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_POST = osc.core.http_POST
http_PUT = osc.core.http_PUT

class FccFreezer(object):
    def __init__(self):
        self.factory = 'openSUSE:Factory'
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']

    def list_packages(self, project):
        url = makeurl(self.apiurl, ['source', project])
        pkglist = []

        root = ET.parse(http_GET(url)).getroot()
        xmllines = root.findall("./entry")
        for pkg in xmllines:
            pkglist.append(pkg.attrib['name'])

        return set(pkglist)

    def check_one_source(self, flink, si, pkglist):
        package = si.get('package')
        logging.debug("Processing %s" % (package))

        # If the package is an internal one (e.g _product)
        if package.startswith('_') or package.startswith('Test-DVD'):
            return None

        for linked in si.findall('linked'):
            if linked.get('project') == self.factory:
                if linked.get('package') in pkglist:
                    return package
                url = makeurl(self.apiurl, ['source', self.factory, package], {'view': 'info', 'nofilename': '1'})
                # print(package, linked.get('package'), linked.get('project'))
                f = http_GET(url)
                proot = ET.parse(f).getroot()
                lsrcmd5 = proot.get('lsrcmd5')
                if lsrcmd5 is None:
                    raise Exception("{}/{} is not a link but we expected one".format(self.factory, package))
                ET.SubElement(flink, 'package', {'name': package, 'srcmd5': lsrcmd5, 'vrev': si.get('vrev')})
                return package

        if package in pkglist:
            return package

        if package in ['rpmlint-mini-AGGR']:
            return package  # we should not freeze aggregates

        ET.SubElement(flink, 'package', {'name': package, 'srcmd5': si.get('srcmd5'), 'vrev': si.get('vrev')})
        return package

    def receive_sources(self, flink, sources):
        pkglist = self.list_packages(OPENSUSE)

        url = makeurl(self.apiurl, ['source', self.factory], {'view': 'info', 'nofilename': '1'})
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for si in root.findall('sourceinfo'):
            package = self.check_one_source(flink, si, pkglist)
            sources[package] = 1
        return sources

    def freeze(self):
        """Main method"""
        sources = {}
        flink = ET.Element('frozenlinks')

        fl = ET.SubElement(flink, 'frozenlink', {'project': self.factory})
        sources = self.receive_sources(fl, sources)

        url = makeurl(self.apiurl, ['source', FCC, '_project', '_frozenlinks'], {'meta': '1'})
        l = ET.tostring(flink)
        try:
            http_PUT(url, data=l)
        except urllib2.HTTPError, e:
            raise e

class FccSubmitter(object):
    def __init__(self, from_prj, to_prj, submit_limit):
        self.from_prj = from_prj
        self.to_prj = to_prj
        self.factory = 'openSUSE:Factory'
        self.submit_limit = int(submit_limit)
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        self.sle_base_prjs = [
                'SUSE:SLE-12-SP2:GA',
                'SUSE:SLE-12-SP1:Update',
                'SUSE:SLE-12-SP1:GA',
                'SUSE:SLE-12:Update',
                'SUSE:SLE-12:GA',
                ]
        # the skip list against devel project
        self.skip_devel_project_list = [
                'mobile:synchronization:FACTORY'
                ]
        # put the except packages from skip_devel_project_list, use regex in this list, eg. "^golang-x-(\w+)", "^nodejs$"
        self.except_pkgs_list = []
        # put the exact package name here
        self.skip_pkgs_list = [
                'python-pypuppetdb$',
                'smbtad',
                'mdds-1_2',
                '^e17',
                'shellementary',
                'aer-inject',
                'xplatproviders',
                'newlib',
                'openttd-openmsx',
                'tulip',
                'guake',
                'mlterm',
                'uim',
                '^libxml',
                'w3m-el',
                'scim$',
                '^scim-(\w+)',
                'gstreamer-0_10-plugins-gl',
                'libgdamm',
                'gtk3-metatheme-sonar',
                'gstreamer-0_10-plugin-crystalhd',
                'grisbi',
                'heroes-tron',
                'specto',
                'wayland-protocols',
                'gsf-sharp',
                'hal-flash',
                'kdelibs3',
                'qca-sasl',
                'mozaddon-gnotifier',
                'khunphan',
                'lxcfs',
                'containerd',
                'docker-bench-security',
                '0ad-data',
                'python-plaso',
                'gnome-news',
                'wdm',
                'nuntius',
                'gobby04',
                'jamin',
                '^bundle-lang',
                'docker-image-migrator',
                'kiwi-config-openSUSE'
                ]
        self.check_later = [
                'tulip',
                'khunphan',
                ]

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        root = ET.parse(http_GET(makeurl(self.apiurl,['source', project],
                                 query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]
        
        return packages

    def get_request_list(self, package):
        return osc.core.get_request_list(self.apiurl, self.to_prj, package, req_state=('new', 'review'))

    def get_link(self, project, package):
        try:
            link = http_GET(makeurl(self.apiurl,['source', project, package, '_link'])).read()
        except (urllib2.HTTPError, urllib2.URLError):
            return None
        return ET.fromstring(link)

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

    def is_new_package(self, tgt_project, tgt_package):
        try:
            logging.debug("Gathering package_meta %s/%s" % (tgt_project, tgt_package))
            osc.core.show_package_meta(self.apiurl, tgt_project, tgt_package)
        except (urllib2.HTTPError, urllib2.URLError):
            return True
        return False

    def get_devel_project(self, package):
        m = osc.core.show_package_meta(self.apiurl, self.factory, package)
        node = ET.fromstring(''.join(m)).find('devel')
        if node is None:
            return None, None
        else:
            return node.get('project'), node.get('package', None)

    def add_review(self, requestid, by_project=None, by_package=None, msg=None):
        query = {}
        query['by_project'] = by_project
        query['by_package'] = by_package
        if not msg:
            msg = "Being evaluated by {}/{}. This is submitted by a tool to Leap, please review this change and decline it if Leap do not need this!"
            msg = msg.format(by_project, by_package)

        if not query:
            raise oscerr.WrongArgs('We need a project')

        query['cmd'] = 'addreview'
        url = makeurl(self.apiurl, ['request', str(requestid)], query)
        http_POST(url, data=msg)

    def create_submitrequest(self, package):
        """Create a submit request using the osc.commandline.Osc class."""
        src_project = self.factory # submit from Factory only
        dst_project = self.to_prj

        msg = 'Automatic request from %s by F-C-C Submitter. Please review this change and decline it if Leap do not need it.' % src_project
        res = osc.core.create_submit_request(self.apiurl,
                                             src_project,
                                             package,
                                             dst_project,
                                             package,
                                             message=msg)
        return res

    def check_multiple_specfiles(self, project, package):
        try:
            url = makeurl(self.apiurl, ['source', project, package], { 'expand': '1' } )
        except urllib2.HTTPError, e:
            if e.code == 404:
                return None
            raise e
        root = ET.fromstring(http_GET(url).read())
        linkinfo = root.find('linkinfo')
        files = [ entry.get('name').replace('.spec', '') for entry in root.findall('entry') if entry.get('name').endswith('.spec') ]
        if linkinfo is not None and len(files) > 1:
            return linkinfo.attrib['package']
        else:
            return False

    def is_sle_base_pkgs(self, package):
        link = self.get_link(self.to_prj, package)
        if link is None or link.get('project') not in self.sle_base_prjs:
            logging.debug("%s not from SLE base"%package)
            return False
        return True

    def list_pkgs(self):
        """List build succeeded packages"""
        succeeded_packages = []
        succeeded_packages = self.get_build_succeeded_packages(self.from_prj)
        if not len(succeeded_packages) > 0:
            logging.info('No build succeeded package in %s'%self.from_prj)
            return

        print 'Build succeeded packages:'
        print '-------------------------------------'
        for pkg in succeeded_packages:
           print pkg

        print '-------------------------------------'
        print "Found {} build succeded packages".format(len(succeeded_packages))

    def get_deleted_packages(self, project):
        query = 'states=accepted&types=delete&project={}&view=collection'
        query = query.format(project)
        url = makeurl(self.apiurl, ['request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()

        pkgs = []
        for sr in root.findall('request'):
            tgt_package = sr.find('action').find('target').get('package')
            pkgs.append(tgt_package)

        return pkgs

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
        deleted_packages = self.get_deleted_packages(self.to_prj)

        ms_packages = [] # collect multi specs packages

        for i in range(0, min(int(self.submit_limit), len(succeeded_packages))):
            package = succeeded_packages[i]

            if package in deleted_packages:
                logging.info('%s has been dropped from %s, ignore it!'%(package, self.to_prj))
                continue

            if self.is_sle_base_pkgs(package) is True:
                logging.info('%s origin from SLE base, skip for now!'%package)
                continue

            # make sure it is new package
            new_pkg = self.is_new_package(self.to_prj, package)
            if new_pkg is not True:
                logging.info('%s is not a new package, do not submit.' % package)
                continue

            multi_specs = self.check_multiple_specfiles(self.factory, package)
            if multi_specs is None:
                logging.info('%s does not exist in %s'%(package, 'openSUSE:Factory'))
                continue

            if multi_specs:
                logging.info('%s in %s have multiple specs, it is linked to %s, skip it!'%(package, 'openSUSE:Factory', multi_specs))
                ms_packages.append(package)
                continue

            # make sure the package non-exist in target yet ie. expand=False
            if package not in target_packages:
                # make sure there is no request against same package
                request = self.get_request_list(package)
                if request:
                    logging.debug("There is a request to %s / %s already, skip!"%(package, self.to_prj))
                else:
                    logging.info("%d - Preparing submit %s to %s"%(i, package, self.to_prj))
                    # get devel project
                    devel_prj, devel_pkg = self.get_devel_project(package)
                    # check devel project does not in the skip list
                    if devel_prj in self.skip_devel_project_list:
                        # check the except packages list
                        match = None
                        for elem in self.except_pkgs_list:
                            m = re.search(elem, package)
                            if m is not None:
                                match = True

                        if match is not True:
                            logging.info('%s/%s is in the skip list, do not submit.' % (devel_prj, package))
                            continue
                        else:
                            pass

                    # check package does not in the skip list
                    match = None
                    for elem in self.skip_pkgs_list:
                        m = re.search(elem, package)
                        if m is not None:
                            match = True

                    if match is True:
                        logging.info('%s is in the skip list, do not submit.' % package)
                        continue
                    else:
                        pass

                    res = self.create_submitrequest(package)
                    if res and res is not None:
                        logging.info('Created request %s for %s' % (res, package))
                        # add review by package
                        #logging.info("Adding review by %s/%s"%(devel_prj, devel_pkg))
                        #self.add_review(res, devel_prj, devel_pkg)
                    else:
                        logging.error('Error occurred when creating submit request')
            else:
                logging.debug('%s is exist in %s, skip!'%(package, self.to_prj))

        # dump multi specs packages
        print("Multi-specfile packages:")
        if ms_packages:
            for pkg in ms_packages:
                print pkg
        else:
            print 'None'



def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    if args.freeze:
        print "freezing {}".format(FCC)
        freezer = FccFreezer()
        freezer.freeze()
    else:
        uc = FccSubmitter(args.from_prj, args.to_prj, args.submit_limit)
        if args.list_packages:
            uc.list_pkgs()
        else:
            uc.crawl()

if __name__ == '__main__':
    description = 'Create SR from openSUSE:42:Factory-Candidates-Check to '\
                  'openSUSE:Leap:42.2 project for new build succeded packages.'
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
    parser.add_argument('-r', '--freeze', dest='freeze', action='store_true', help='rebase FCC project')
    parser.add_argument('-s', '--list', dest='list_packages', action='store_true', help='list build succeeded packages')
    parser.add_argument('-l', '--limit', dest='submit_limit', metavar='NUMBERS', help='limit numbers packages to submit (default: 100)', default=100)

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
