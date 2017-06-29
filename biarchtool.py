#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2015 SUSE Linux GmbH
# Copyright (c) 2016 SUSE LLC
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

from xml.etree import cElementTree as ET
import sys
import cmdln
import logging
import urllib2
import osc.core

import ToolBase

logger = logging.getLogger()

FACTORY = "openSUSE:Factory"

class BiArchTool(ToolBase.ToolBase):

    def __init__(self, project):
        ToolBase.ToolBase.__init__(self)
        self.project = project
        self.biarch_packages = None
        self._has_baselibs = dict()
        self.packages = []
        self.arch = 'i586'
        self.rdeps = None
        self.whitelist = {
                'i586': set([
                    'bzr',
                    'git',
                    # _link to baselibs package
                    'libjpeg62-turbo',
                    'mercurial',
                    'subversion',
                    'ovmf'
                    ]),
                }
        self.blacklist = {
                'i586': set([
                    'belle-sip',
                    'release-notes-openSUSE',
                    'skelcd-openSUSE',
                    'plasma5-workspace',
                    ]),
                }

    def has_baselibs(self, package):
        if package in self._has_baselibs:
            return self._has_baselibs[package]

        ret = False
        files = ET.fromstring(self.cached_GET(self.makeurl(['source', self.project, package])))
        for n in files.findall("./entry[@name='baselibs.conf']"):
            logger.debug('%s has baselibs', package)
            ret = True
            break
        self._has_baselibs[package] = ret
        #logger.debug('%s has no baselibs', package)
        return ret

    def is_biarch_recursive(self, package):
        if package in self.blacklist[self.arch]:
            logger.debug('%s is blacklisted', package)
            return False
        if package in self.biarch_packages:
            logger.debug('%s is known biarch package', package)
            return True
        if package in self.whitelist[self.arch]:
            logger.debug('%s is whitelisted', package)
            return True
        r = self.has_baselibs(package)
        if r:
            return r
        if package in self.rdeps:
            #logger.debug('checking rdeps for %s (%d)', package, len(self.rdeps[package]))
            for p in self.rdeps[package]:
                r = self.is_biarch_recursive(p)
                if r:
                    break
        return r

    def _init_biarch_packages(self):
        if self.biarch_packages is None:
            self.biarch_packages = set(self.meta_get_packagelist("%s:Rings:0-Bootstrap"%self.project))
            self.biarch_packages |= set(self.meta_get_packagelist("%s:Rings:1-MinimalX"%self.project))

        self._init_rdeps()

    def _init_rdeps(self):
        if self.rdeps is not None:
            return
        self.rdeps = dict()
        url = self.makeurl(['build', self.project, 'standard', self.arch, '_builddepinfo' ], {'view':'revpkgnames'})
        x = ET.fromstring(self.cached_GET(url))
        for pnode in x.findall('package'):
            name = pnode.get('name')
            for depnode in pnode.findall('pkgdep'):
                depname = depnode.text
                self.rdeps.setdefault(name, set()).add(depname)

    def select_packages(self, packages):
        if packages == '__all__':
            self.packages = self.meta_get_packagelist(self.project)
        elif packages == '__latest__':
            # only works when called in packagelists loop
            #self.packages = self._filter_packages_by_time(self.latest_packages(self.project))
            self.packages = self.latest_packages(self.project)
        else:
            self.packages = packages

    # check when _product was last changed, eg by packagelist
    # generator. Yield only packges that got checked in after that
    # point in time.
    def _filter_packages_by_time(self, packages):
        x = ET.fromstring(self.cached_GET(self.makeurl(['source', self.project, '_product', '_history'], {'limit':'1'})))
        producttime = int(x.find('./revision/time').text)
        for pkg in packages:
            try:
                x = ET.fromstring(self.cached_GET(self.makeurl(['source', self.project, pkg, '_history'], {'rev':'1'})))
            # catch deleted packages
            except urllib2.HTTPError, e:
                if e.code == 404:
                    continue
                raise e

            packagetime = int(x.find('./revision/time').text)
#            if producttime > packagetime:
#                continue
            yield pkg

    def remove_explicit_enable(self):

        self._init_biarch_packages()

        resulturl = self.makeurl(['build', self.project, '_result'])
        result = ET.fromstring(self.cached_GET(resulturl))

        packages = set()

        for n in result.findall("./result[@arch='{}']/status".format(self.arch)):
            if n.get('code') not in ('disabled', 'excluded'):
                #logger.debug('%s %s', n.get('package'), n.get('code'))
                packages.add(n.get('package'))

        for pkg in sorted(packages):
            changed = False

            logger.debug("processing %s", pkg)
            pkgmetaurl = self.makeurl(['source', self.project, pkg, '_meta'])
            pkgmeta = ET.fromstring(self.cached_GET(pkgmetaurl))

            for build in pkgmeta.findall("./build"):
                for n in build.findall("./enable[@arch='{}']".format(self.arch)):
                    logger.debug("disable %s", pkg)
                    build.remove(n)
                    changed = True

            if changed:
                try:
                    self.http_PUT(pkgmetaurl, data=ET.tostring(pkgmeta))
                    if self.caching:
                        self._invalidate__cached_GET(pkgmetaurl)
                except urllib2.HTTPError, e:
                    logger.error('failed to update %s: %s', pkg, e)

    def add_explicit_disable(self, wipebinaries=False):

        self._init_biarch_packages()

        resulturl = self.makeurl(['source', self.project])
        result = ET.fromstring(self.cached_GET(resulturl))

        for pkg in self.packages:

            changed = False

            logger.debug("processing %s", pkg)
            pkgmetaurl = self.makeurl(['source', self.project, pkg, '_meta'])
            pkgmeta = ET.fromstring(self.cached_GET(pkgmetaurl))

            build = pkgmeta.findall("./build")
            if not build:
                logger.debug('disable %s for %s', pkg, self.arch)
                bn = pkgmeta.find('build')
                if bn is None:
                    bn = ET.SubElement(pkgmeta, 'build')
                ET.SubElement(bn, 'disable', { 'arch' : self.arch })
                changed = True

            if changed:
                try:
                    self.http_PUT(pkgmetaurl, data=ET.tostring(pkgmeta))
                    if self.caching:
                        self._invalidate__cached_GET(pkgmetaurl)
                    if wipebinaries:
                        self.http_POST(self.makeurl(['build', self.project], {
                            'cmd' : 'wipe',
                            'arch': self.arch,
                            'package' : pkg }))
                except urllib2.HTTPError, e:
                    logger.error('failed to update %s: %s', pkg, e)


    def enable_baselibs_packages(self, force=False, wipebinaries=False):
        self._init_biarch_packages()
        for pkg in self.packages:
            logger.debug("processing %s", pkg)
            pkgmetaurl = self.makeurl(['source', self.project, pkg, '_meta'])
            try:
                pkgmeta = ET.fromstring(self.cached_GET(pkgmetaurl))
            except urllib2.HTTPError, e:
                # catch deleted packages
                if e.code == 404:
                    continue
                raise e

            is_enabled = None
            is_disabled = None
            has_baselibs = None
            must_disable = None
            changed = None

            for n in pkgmeta.findall("./build/enable[@arch='{}']".format(self.arch)):
                is_enabled = True
            for n in pkgmeta.findall("./build/disable[@arch='{}']".format(self.arch)):
                is_disabled = True

            if force:
                must_disable = False

            if must_disable is None:
                if self.is_biarch_recursive(pkg):
                    must_disable = False
                else:
                    must_disable = True

            if must_disable == False:
                if is_disabled:
                    logger.info('enabling %s for %s', pkg, self.arch)
                    for build in pkgmeta.findall("./build"):
                        for n in build.findall("./disable[@arch='{}']".format(self.arch)):
                            build.remove(n)
                            changed = True
                    if changed == False:
                        logger.error('build tag not found in %s/%s!?', pkg, self.arch)
                else:
                    logger.debug('%s already enabled for %s', pkg, self.arch)
            elif must_disable == True:
                if not is_disabled:
                    logger.info('disabling %s for %s', pkg, self.arch)
                    bn = pkgmeta.find('build')
                    if bn is None:
                        bn = ET.SubElement(pkgmeta, 'build')
                    ET.SubElement(bn, 'disable', { 'arch' : self.arch })
                    changed = True
                else:
                    logger.debug('%s already disabled for %s', pkg, self.arch)

            if is_enabled:
                logger.info('removing explicit enable %s for %s', pkg, self.arch)
                for build in pkgmeta.findall("./build"):
                    for n in build.findall("./enable[@arch='{}']".format(self.arch)):
                        build.remove(n)
                        changed = True
                if changed == False:
                    logger.error('build tag not found in %s/%s!?', pkg, self.arch)

            if changed:
                try:
                    self.http_PUT(pkgmetaurl, data=ET.tostring(pkgmeta))
                    if self.caching:
                        self._invalidate__cached_GET(pkgmetaurl)
                    if must_disable and wipebinaries:
                        self.http_POST(self.makeurl(['build', self.project], {
                            'cmd' : 'wipe',
                            'arch': self.arch,
                            'package' : pkg }))
                except urllib2.HTTPError, e:
                    logger.error('failed to update %s: %s', pkg, e)

class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-p', '--project', dest='project', metavar='PROJECT',
                        help='project to process (default: %s)' % FACTORY,
                        default = FACTORY)
        return parser

    def setup_tool(self):
        tool = BiArchTool(self.options.project)
        return tool

    def _select_packages(self, all, packages):
        if packages:
            self.tool.select_packages(packages)
        elif all:
            self.tool.select_packages('__all__')
        else:
            self.tool.select_packages('__latest__')

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    @cmdln.option('-a', '--all', action='store_true', help='process all packages')
    @cmdln.option('-f', '--force', action='store_true', help='enable in any case')
    @cmdln.option('--wipe', action='store_true', help='also wipe binaries')
    def do_enable_baselibs_packages(self, subcmd, opts, *packages):
        """${cmd_name}: enable build for packages in Ring 0 or 1 or with
        baselibs.conf

        ${cmd_usage}
        ${cmd_option_list}
        """
        def work():
            self._select_packages(opts.all, packages)
            self.tool.enable_baselibs_packages(force=opts.force, wipebinaries=opts.wipe)

        self.runner(work, opts.interval)

    @cmdln.option('-a', '--all', action='store_true', help='process all packages')
    def do_remove_explicit_enable(self, subcmd, opts, *packages):
        """${cmd_name}: remove all explicit enable tags from packages

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.remove_explicit_enable()

    @cmdln.option('-a', '--all', action='store_true', help='process all packages')
    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    @cmdln.option('--wipe', action='store_true', help='also wipe binaries')
    def do_add_explicit_disable(self, subcmd, opts, *packages):
        """${cmd_name}: add explicit disable to all packages

        ${cmd_usage}
        ${cmd_option_list}
        """

        def work():
            self._select_packages(opts.all, packages)
            self.tool.add_explicit_disable(wipebinaries=opts.wipe)

        self.runner(work, opts.interval)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
