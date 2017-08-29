#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2017 SUSE LLC
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

from lxml import etree as ET
from collections import namedtuple
import sys
import cmdln
import logging
import urllib2
import osc.core
import glob
import solv
from pprint import pprint, pformat
import os

import ToolBase

logger = logging.getLogger()

FACTORY = "SUSE:SLE-15:GA"
ARCHITECTURES = ('x86_64', 'ppc64le', 's390x')

class Group(object):

    def __init__(self, name, pkglist):
        self.name = name
        self.pkglist = pkglist
        self.conditional = None
        self.packages = dict()
        self.solved_packages = None
        self.solved = False
        self.base = None

    def get_solved_packages_recursive(self, arch):
        if not self.solved:
            raise Exception('group {} not solved'.format(self.name))

        solved = self.solved_packages['*'] | self.solved_packages[arch]
        logger.debug("{}.{} have {} packages".format(self.name, arch, len(solved)))
        if self.base:
            for b in self.base:
                solved |= b.get_solved_packages_recursive(arch)

        return solved

    def solve(self, base = None):
        """ base: list of base groups or None """

        if self.solved:
            return

        solved = dict()
        for arch in ARCHITECTURES:
            pool = solv.Pool()
            pool.setarch(arch)

            # XXX
            repo = pool.add_repo('full')
            repo.add_solv('repo-{}-standard-{}.solv'.format(FACTORY, arch))

            pool.addfileprovides()
            pool.createwhatprovides()

            jobs = []
            toinstall = set(self.packages['*'])
            basepackages = set()
            logger.debug("{}: {} common packages".format(self.name, len(toinstall)))
            if arch in self.packages:
                logger.debug("{}: {} {} packages".format(self.name, arch, len(self.packages[arch])))
                toinstall |= self.packages[arch]
            if base:
                for b in base:
                    logger.debug("{} adding packges from {}".format(self.name, b.name))
                    basepackages |= b.get_solved_packages_recursive(arch)
            toinstall |= basepackages
            for n in toinstall:
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    logger.error('{}.{}: package {} not found'.format(self.name, arch, n))
                jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

            solver = pool.Solver()
            problems = solver.solve(jobs)
            if problems:
                for problem in problems:
                    # just ignore conflicts here
                    #if not ' conflicts with ' in str(problem):
                    logger.error(problem)
                    raise Exception('unresolvable')
                    #logger.warning(problem)

            trans = solver.transaction()
            if trans.isempty():
                raise Exception('nothing to do')

            solved[arch] = set([ s.name for s in trans.newsolvables() ])
            if basepackages:
                self.base = list(base)
                solved[arch] -= basepackages

        common = None
        # compute common packages across all architectures
        for arch in solved.keys():
            if common is None:
                common = set(solved[arch])
                continue
            common &= solved[arch]
        # reduce arch specific set by common ones
        for arch in solved.keys():
            solved[arch] -= common

        self.solved_packages = solved
        self.solved_packages['*'] = common

        self.solved = True

    def architectures(self):
        return self.solved_packages.keys()

    def toxml(self, arch):

        packages = None
        autodeps = None

        if arch in self.solved_packages:
            autodeps = self.solved_packages[arch]

        if arch in self.packages:
            packages = self.packages[arch]
            if autodeps:
                autodeps -= self.packages[arch]

        if not packages and not autodeps:
            return None

        name = self.name
        if arch != '*':
            name += '.' + arch

        root = ET.Element('group', { 'name' : name})
        c = ET.Comment(' ### AUTOMATICALLY GENERATED, DO NOT EDIT ### ')
        root.append(c)

        if self.base:
            for b in self.base:
                c = ET.Comment(' based on {} '.format(', '.join([b.name for b in self.base])))
                root.append(c)

        if arch != '*':
            cond = ET.SubElement(root, 'conditional', {'name': 'only_{}'.format(arch)})
        packagelist = ET.SubElement(root, 'packagelist', {'relationship': 'recommends'})

        if packages:
            for name in sorted(packages):
                p = ET.SubElement(packagelist, 'package', {
                    'name' : name,
                    'supportstatus' : self.pkglist.supportstatus(name)
                    })

        if autodeps:
            c = ET.Comment(' automatic dependencies ')
            packagelist.append(c)

            for name in sorted(autodeps):
                p = ET.SubElement(packagelist, 'package', {
                    'name' : name,
                    'supportstatus' : self.pkglist.supportstatus(name)
                    })

        return root

    def dump(self):
        for arch in sorted(self.architectures()):
            x = self.toxml(arch)
            if x is not None:
                print(ET.tostring(x, pretty_print = True))

class PkgListGen(ToolBase.ToolBase):

    def __init__(self, project):
        ToolBase.ToolBase.__init__(self)
        self.project = project
        # package -> supportatus
        self.packages = dict()
        self.default_support_status = 'l3'
        self.groups = dict()
        self._supportstatus = None

    def _load_supportstatus(self):
        # XXX
        with open('supportstatus.txt', 'r') as fh:
            self._supportstatus = dict()
            for l in fh.readlines():
                group, pkg, status  = l.split(' ')
                self._supportstatus[pkg] = status

    # TODO: make per product
    def supportstatus(self, package):
        if self._supportstatus is None:
            self._load_supportstatus()

        if package in self._supportstatus:
            return self._supportstatus[package]
        else:
            return self.default_support_status

    # XXX: move to group class. just here to check supportstatus
    def _parse_group(self, root):
        groupname = root.get('name')
        group = Group(groupname, self)
        for node in root.findall(".//package"):
            name = node.get('name')
            arch = node.get('arch', '*')
            status = node.get('supportstatus') or ''
#            logger.debug('group %s, package %s, status %s', groupname, name, status)
#            self.packages.setdefault(name, dict())
#            self.packages[name].setdefault(status, set()).add(groupname)
            if name in self.packages and self.packages[name] != status:
                logger.error("%s: support status of %s already is %s, got %s", groupname, name, self.packages[name], status)
            else:
                self.packages[name] = status
            group.packages.setdefault(arch, set()).add(name)
        return group

    def _load_group_file(self, fn):
        with open(fn, 'r') as fh:
            logger.debug("reading %s", fn)
            root = ET.parse(fh).getroot()
            if root.tag == 'group':
                g = self._parse_group(root)
                self.groups[g.name] = g
            else:
                for groupnode in root.findall("./group"):
                    g = self._parse_group(groupnode)
                    self.groups[g.name] = g

    def load_all_groups(self):
        for fn in glob.glob('*.group.in'):
            self._load_group_file(fn)

    def _write_all_groups(self):
        for name in self.groups:
            group = self.groups[name]
            if not group.solved:
                logger.error('{} not solved'.format(name))
                continue
            for arch in sorted(group.architectures()):
                if arch != '*':
                    fn = '{}.{}.group'.format(name, arch)
                else:
                    fn = '{}.group'.format(name)
                x = group.toxml(arch)
                if x is None:
                    os.unlink(fn)
                else:
                    with open(fn, 'w') as fh:
                        fh.write(ET.tostring(x, pretty_print = True))

    def _parse_product(self, root):
        print(root.find('.//products/product/name').text)
        for mnode in root.findall(".//mediasets/media"):
            name = mnode.get('name')
            print('  {}'.format(name))
            for node in mnode.findall(".//use"):
                print('    {}'.format(node.get('group')))

    def list_products(self):
        for fn in glob.glob('*.product'):
            with open(fn, 'r') as fh:
                logger.debug("reading %s", fn)
                root = ET.parse(fh).getroot()
                self._parse_product(root)

    def solve_group(self, name):
        self._load_all_groups()
        group = self.groups[name]
        group.solve()
        return group
 
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
        tool = PkgListGen(self.options.project)
        return tool


    def do_list(self, subcmd, opts):
        """${cmd_name}: list all groups

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()

        for name in sorted(self.tool.groups.keys()):
            print name

    def do_list_products(self, subcmd, opts):
        """${cmd_name}: list all products

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.list_products()

    def do_solve_group(self, subcmd, opts, name):
        """${cmd_name}: list all products

        ${cmd_usage}
        ${cmd_option_list}
        """

        group = self.tool.solve_group(name)
        for arch in sorted(group.architectures()):
            print(ET.tostring(group.toxml(arch), pretty_print = True))

    def do_solveall(self, subcmd, opts):
        """${cmd_name}: list all products

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()

        bootloader = self.tool.groups["bootloader"]
        caasp = self.tool.groups["CAASP-DVD-Packages"] # XXX
        dictionaries = self.tool.groups["dictionaries"]
        icewm = self.tool.groups["desktop_icewm"] # XXX
        legacy = self.tool.groups["legacy"]
        nvdimm = self.tool.groups["nvdimm"]
        ofed = self.tool.groups["ofed"]
        perl = self.tool.groups["perl"]
        public_cloud = self.tool.groups["public_cloud"]
        python = self.tool.groups["python"]
        python_f = self.tool.groups["python_f"]
        release_packages_sles = self.tool.groups["release_packages_sles"]
        release_packages_sled = self.tool.groups["release_packages_sled"]
        release_packages_leanos = self.tool.groups["release_packages_leanos"]
        sap_applications = self.tool.groups["sap_applications"]
        sle_base = self.tool.groups["sle_base"]
        sle_minimal = self.tool.groups["sle_minimal"]
        update_test = self.tool.groups["update-test"]

        sle_minimal.solve()
        sle_base.solve(base = [sle_minimal])

        bootloader.solve(base = [sle_base])

        python.solve(base = [sle_base])
        python_f.solve(base = [sle_base])

#        sle_base.dump()

        self.tool._write_all_groups()

#        group = self.tool.solve_group(name)
#        for arch in sorted(group.architectures()):
#            print(ET.tostring(group.toxml(arch), pretty_print = True))

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
