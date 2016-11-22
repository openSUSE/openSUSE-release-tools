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

makeurl = osc.core.makeurl

logger = logging.getLogger()

FACTORY = "openSUSE:Factory"

class BiArchTool(ToolBase.ToolBase):

    def __init__(self, project):
        ToolBase.ToolBase.__init__(self)
        self.project = project
        self.biarch_packages = None
        self.packages = []
        self.arch = 'i586'

    def _init_biarch_packages(self):
        if self.biarch_packages is None:
            self.biarch_packages = set(self.meta_get_packagelist("%s:Rings:0-Bootstrap"%self.project))
            self.biarch_packages |= set(self.meta_get_packagelist("%s:Rings:1-MinimalX"%self.project))

    def select_packages(self, packages):
        if packages == '__all__':
            self.packages = self.meta_get_packagelist(self.project)
        elif packages == '__latest__':
            self.packages = self.latest_packages(self.project)
        else:
            self.packages = packages

    def enable_baselibs_packages(self, force=False):
        self._init_biarch_packages()
        for pkg in self.packages:
            logger.debug("processing %s", pkg)
            pkgmetaurl = makeurl(self.apiurl, ['source', self.project, pkg, '_meta'])
            pkgmeta = ET.fromstring(self.cached_GET(pkgmetaurl))
            is_enabled = None
            is_disabled = None
            has_baselibs = None
            must_enable = None
            changed = None

            if force:
                must_enable = True

            for n in pkgmeta.findall("./build/enable[@arch='{}']".format(self.arch)):
                is_enabled = True
                break
            for n in pkgmeta.findall("./build/disable[@arch='{}']".format(self.arch)):
                is_disabled = True
                break
            if pkg in self.biarch_packages:
                logger.debug('%s is known biarch package', pkg)
                must_enable = True
            else:
                files = ET.fromstring(self.cached_GET(makeurl(self.apiurl, ['source', self.project, pkg])))
                for n in files.findall("./entry[@name='baselibs.conf']"):
                    has_baselibs = True
                    logger.debug('%s has baselibs', pkg)
                    break
            if has_baselibs:
                must_enable = True

            if must_enable:
                if is_disabled:
                    logger.warn('%s should be enabled but is disabled', pkg)
                if not is_enabled:
                    logger.info('enabling %s for biarch', pkg)
                    bn = pkgmeta.find('build')
                    if bn is None:
                        bn = ET.SubElement(pkgmeta, 'build')
                    ET.SubElement(bn, 'enable', { 'arch' : self.arch })
                    changed = True
            else:
                if is_enabled:
                    logger.warn("%s enabled or biarch without need", pkg)

            if changed:
                try:
                    self.http_PUT(pkgmetaurl, data=ET.tostring(pkgmeta))
                    if self.caching:
                        self._invalidate__cached_GET(pkgmetaurl)
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
    def do_enable_baselibs_packages(self, subcmd, opts, *packages):
        """${cmd_name}: enable build for packages in Ring 0 or 1 or with
        baselibs.conf

        ${cmd_usage}
        ${cmd_option_list}
        """
        def work():
            self._select_packages(opts.all, packages)
            self.tool.enable_baselibs_packages(force=opts.force)

        self.runner(work, opts.interval)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
