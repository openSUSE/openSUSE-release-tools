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

# TODO: implement equivalent of namespace namespace:language(de) @SYSTEM
# TODO: solve all devel packages to include

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
import subprocess
import re
import yaml

import ToolBase

# share header cache with repochecker
from osclib.memoize import CACHEDIR

logger = logging.getLogger()

ARCHITECTURES = ('x86_64', 'ppc64le', 's390x', 'aarch64')
APIURL = 'https://api.suse.de/public/'


class Group(object):

    def __init__(self, name, pkglist):
        self.name = name
        self.safe_name = re.sub(r'\W', '_', name.lower())
        self.pkglist = pkglist
        self.conditional = None
        self.packages = dict()
        self.locked = set()
        self.solved_packages = None
        self.solved = False
        self.not_found = dict()
        self.unresolvable = dict()
        for a in ARCHITECTURES:
            self.packages[a] = []
            self.unresolvable[a] = dict()

        self.srcpkgs = None
        self.develpkgs = []
        self.silents = set()

        pkglist.groups[self.safe_name] = self

    def _add_to_packages(self, package, arch=None):
        archs = ARCHITECTURES
        if arch:
            archs = [arch]

        for a in archs:
            self.packages[a].append([package, self.name])

    def parse_yml(self, packages):
        # package less group is a rare exception
        if packages is None:
            return

        for package in packages:
            if not isinstance(package, dict):
                self._add_to_packages(package)
                continue
            name = package.keys()[0]
            for rel in package[name]:
                if rel == 'locked':
                    self.locked.add(name)
                elif rel == 'silent':
                    self._add_to_packages(name)
                    self.silents.add(name)
                else:
                    self._add_to_packages(name, rel)

    def _verify_solved(self):
        if not self.solved:
            raise Exception('group {} not solved'.format(self.name))

    def inherit(self, group):
        for arch in ARCHITECTURES:
            self.packages[arch] += group.packages[arch]

        self.locked.update(group.locked)
        self.silents.update(group.silents)

    # do not repeat packages
    def ignore(self, without):
        for arch in ('*', ) + ARCHITECTURES:
            s = set(without.solved_packages[arch].keys())
            s |= set(without.solved_packages['*'].keys())
            for p in s:
                self.solved_packages[arch].pop(p, None)
        for p in without.not_found.keys():
            if not p in self.not_found:
                continue
            self.not_found[p] -= without.not_found[p]
            if not self.not_found[p]:
                self.not_found.pop(p)

    def solve(self, ignore_recommended=True):
        """ base: list of base groups or None """

        if self.solved:
            return

        solved = dict()
        for arch in ARCHITECTURES:
            solved[arch] = dict()

        self.srcpkgs = set()
        self.recommends = dict()
        for arch in ARCHITECTURES:
            pool = self.pkglist._prepare_pool(arch)
            # pool.set_debuglevel(10)

            for n, group in self.packages[arch]:
                jobs = list(self.pkglist.lockjobs[arch])
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    logger.debug('{}.{}: package {} not found'.format(self.name, arch, n))
                    self.not_found.setdefault(n, set()).add(arch)
                    continue
                else:
                    jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

                for l in self.locked:
                    sel = pool.select(str(l), solv.Selection.SELECTION_NAME)
                    if sel.isempty():
                        logger.warn('{}.{}: locked package {} not found'.format(self.name, arch, l))
                    else:
                        jobs += sel.jobs(solv.Job.SOLVER_LOCK)

                for s in self.silents:
                    sel = pool.select(str(s), solv.Selection.SELECTION_NAME | solv.Selection.SELECTION_FLAT)
                    if sel.isempty():
                        logger.warn('{}.{}: silent package {} not found'.format(self.name, arch, s))
                    else:
                        jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

                solver = pool.Solver()
                if ignore_recommended:
                    solver.set_flag(solver.SOLVER_FLAG_IGNORE_RECOMMENDED, 1)

                problems = solver.solve(jobs)
                if problems:
                    for problem in problems:
                        logger.debug('unresolvable: %s.%s: %s', self.name, arch, problem)
                        self.unresolvable[arch][n] = str(problem)
                    continue

                trans = solver.transaction()
                if trans.isempty():
                    logger.error('%s.%s: nothing to do', self.name, arch)
                    continue

                for s in solver.get_recommended():
                    self.recommends.setdefault(s.name, group + ':' + n)

                for s in trans.newsolvables():
                    solved[arch].setdefault(s.name, group + ':' + n)
                    reason, rule = solver.describe_decision(s)
                    if None:
                        print(self.name, s.name, reason, rule.info().problemstr())
                    # don't ask me why, but that's how it seems to work
                    if s.lookup_void(solv.SOLVABLE_SOURCENAME):
                        src = s.name
                    else:
                        src = s.lookup_str(solv.SOLVABLE_SOURCENAME)
                    self.srcpkgs.add(src)

        common = None
        # compute common packages across all architectures
        for arch in ARCHITECTURES:
            if common is None:
                common = set(solved[arch].keys())
                continue
            common &= set(solved[arch].keys())

        if common is None:
            common = set()

        # reduce arch specific set by common ones
        solved['*'] = dict()
        for arch in ARCHITECTURES:
            for p in common:
                solved['*'][p] = solved[arch].pop(p)

        self.solved_packages = solved
        self.solved = True

    def collect_devel_packages(self, modules):
        develpkgs = set()
        for arch in ARCHITECTURES:
            pool = self.pkglist._prepare_pool(arch)
            sel = pool.Selection()
            for s in pool.solvables_iter():
                if s.name.endswith('-devel'):
                    # don't ask me why, but that's how it seems to work
                    if s.lookup_void(solv.SOLVABLE_SOURCENAME):
                        src = s.name
                    else:
                        src = s.lookup_str(solv.SOLVABLE_SOURCENAME)

                    if src in self.srcpkgs:
                        develpkgs.add(s.name)

        self.develpkgs = []
        for p in develpkgs:
            already_present = False
            for m in modules:
                for arch in ('*', ) + ARCHITECTURES:
                    already_present = already_present or (p in m.solved_packages[arch])
                    already_present = already_present or (p in m.develpkgs)
            if not already_present:
                self.develpkgs.append(p)

    def filter_duplicated_recommends(self, modules):
        recommends = self.recommends
        # erase our own - so we don't filter our own
        self.recommends = dict()
        for p in recommends:
            already_present = False
            for m in modules:
                for arch in ('*', ) + ARCHITECTURES:
                    already_present = already_present or (p in m.solved_packages[arch])
                    already_present = already_present or (p in m.recommends)
            if not already_present:
                self.recommends[p] = recommends[p]

    def toxml(self, arch):
        packages = self.solved_packages[arch]

        name = self.name
        if arch != '*':
            name += '.' + arch

        root = ET.Element('group', {'name': name})
        c = ET.Comment(' ### AUTOMATICALLY GENERATED, DO NOT EDIT ### ')
        root.append(c)

        if arch != '*':
            cond = ET.SubElement(root, 'conditional', {
                                 'name': 'only_{}'.format(arch)})
        packagelist = ET.SubElement(
            root, 'packagelist', {'relationship': 'recommends'})

        missing = dict()
        if arch == '*':
            missing = self.not_found
        unresolvable = self.unresolvable.get(arch, dict())
        for name in sorted(packages.keys() + missing.keys() + unresolvable.keys()):
            if name in self.silents:
                continue
            if name in missing:
                c = ET.Comment(' {} not found on {}'.format(name, ','.join(sorted(missing[name]))))
                packagelist.append(c)
                continue
            if name in unresolvable:
                c = ET.Comment(' {} uninstallable: {}'.format(name, unresolvable[name]))
                packagelist.append(c)
                continue
            status = self.pkglist.supportstatus(name)
            p = ET.SubElement(packagelist, 'package', {
                'name': name,
                'supportstatus': status})
            c = ET.Comment(' reason: {} '.format(packages[name]))
            packagelist.append(c)
        if arch == '*' and self.develpkgs:
            c = ET.Comment("\nDevelopment packages:\n  - " + "\n  - ".join(sorted(self.develpkgs)) + "\n")
            root.append(c)
        if arch == '*' and self.recommends:
            comment = "\nRecommended packages:\n"
            for p in sorted(self.recommends.keys()):
                comment += "  - {} # {}\n".format(p, self.recommends[p])
            c = ET.Comment(comment)
            root.append(c)

        return root

    def dump(self):
        pprint({'name': self.name, 'missing': self.missing, 'packages': self.packages,
                'solved': self.solved_packages, 'silents': self.silents})
        return
        archs = ('*',) + ARCHITECTURES
        for arch in archs:
            x = self.toxml(arch)
            print(ET.tostring(x, pretty_print=True))


class PkgListGen(ToolBase.ToolBase):

    def __init__(self, repostr):
        ToolBase.ToolBase.__init__(self)
        self.repos = []
        for repo in repostr.split(','):
            project, reponame = repo.split('/')
            self.repos.append({'project': project, 'repo': reponame})
        # package -> supportatus
        self.packages = dict()
        self.default_support_status = 'l3'
        self.groups = dict()
        self._supportstatus = None
        self.input_dir = '.'
        self.output_dir = '.'
        self.lockjobs = dict()

    def _dump_supportstatus(self):
        for name in self.packages.keys():
            for status in self.packages[name]:
                if status == self.default_support_status:
                    continue
                for group in self.packages[name][status]:
                    print name, status

    def _load_supportstatus(self):
        # XXX
        with open(os.path.join(self.input_dir, 'supportstatus.txt'), 'r') as fh:
            self._supportstatus = dict()
            for l in fh:
                # pkg, status
                a = l.rstrip().split(' ')
                if len(a) > 1:
                    self._supportstatus[a[0]] = a[1]

    # TODO: make per product
    def supportstatus(self, package):
        if self._supportstatus is None:
            self._load_supportstatus()

        if package in self._supportstatus:
            return self._supportstatus[package]
        else:
            return self.default_support_status

    def _load_group_file(self, fn):
        output = None
        with open(fn, 'r') as fh:
            logger.debug("reading %s", fn)
            for groupname, group in yaml.safe_load(fh).items():
                if groupname == 'OUTPUT':
                    output = group
                    continue
                g = Group(groupname, self)
                g.parse_yml(group)
        return output

    def load_all_groups(self):
        output = None
        for fn in glob.glob(os.path.join(self.input_dir, 'group*.yml')):
            o = self._load_group_file(fn)
            if not output:
                output = o
        return output

    def _write_all_groups(self):
        self._check_supplements()
        archs = ('*',) + ARCHITECTURES
        for name in self.groups:
            group = self.groups[name]
            fn = '{}.group'.format(group.name)
            if not group.solved:
                continue
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                for arch in archs:
                    x = group.toxml(arch)
                    x = ET.tostring(x, pretty_print=True)
                    x = re.sub('\s*<!-- reason:', ' <!-- reason:', x)
                    # fh.write(ET.tostring(x, pretty_print = True, doctype = '<?xml version="1.0" encoding="UTF-8"?>'))
                    fh.write(x)

    def _parse_product(self, root):
        print(root.find('.//products/product/name').text)
        for mnode in root.findall(".//mediasets/media"):
            name = mnode.get('name')
            print('  {}'.format(name))
            for node in mnode.findall(".//use"):
                print('    {}'.format(node.get('group')))

    def list_products(self):
        for fn in glob.glob('*.product'):
            with open(os.path.join(self.input_dir, fn), 'r') as fh:
                logger.debug("reading %s", fn)
                root = ET.parse(fh).getroot()
                self._parse_product(root)

    def solve_module(self, groupname, includes, excludes):
        g = self.groups[groupname]
        for i in includes:
            g.inherit(self.groups[i])
        g.solve()
        for e in excludes:
            g.ignore(self.groups[e])

    def _check_supplements(self):
        tocheck = set()
        for arch in ARCHITECTURES:
            pool = self._prepare_pool(arch)
            sel = pool.Selection()
            for s in pool.solvables_iter():
                sel.add_raw(solv.Job.SOLVER_SOLVABLE, s.id)

            for s in sel.solvables():
                for dep in s.lookup_deparray(solv.SOLVABLE_SUPPLEMENTS):
                    for d in dep.str().split(' '):
                        if d.startswith('namespace:modalias') or d.startswith('namespace:filesystem'):
                            tocheck.add(s.name)

        all_grouped = set()
        for g in self.groups.values():
            if g.solved:
                for arch in g.solved_packages.keys():
                    if g.solved_packages[arch]:
                        all_grouped.update(g.solved_packages[arch])

        for p in tocheck - all_grouped:
            logger.warn('package %s has supplements but is not grouped', p)

    def _prepare_pool(self, arch):
        pool = solv.Pool()
        pool.setarch(arch)

        self.lockjobs[arch] = []
        solvables = set()
        for prp in self.repos:
            project = prp['project']
            reponame = prp['repo']
            repo = pool.add_repo(project)
            s = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, reponame, arch))
            r = repo.add_solv(s)
            if not r:
                raise Exception("failed to add repo {}/{}/{}. Need to run update first?".format(project, reponame, arch))
            for solvable in repo.solvables_iter():
		if solvable.name in solvables:
                     self.lockjobs[arch].append(pool.Job(solv.Job.SOLVER_SOLVABLE|solv.Job.SOLVER_LOCK, solvable.id))
                solvables.add(solvable.name)

        pool.addfileprovides()
        pool.createwhatprovides()

        return pool

    def _collect_unsorted_packages(self):
        return
        packages = dict()
        for arch in ARCHITECTURES:
            pool = self._prepare_pool(arch)
            sel = pool.Selection()
            p = set([s.name for s in
                     pool.solvables_iter() if not
                     (s.name.endswith('-debuginfo') or
                      s.name.endswith('-debugsource'))])

            for g in self.groups.values():
                if g.solved:
                    for a in ('*', arch):
                        p -= g.solved_packages[a]
            packages[arch] = p

        common = None
        # compute common packages across all architectures
        for arch in packages.keys():
            if common is None:
                common = set(packages[arch])
                continue
            common &= packages[arch]

        # reduce arch specific set by common ones
        for arch in packages.keys():
            packages[arch] -= common

        packages['*'] = common

        g = Group('unsorted', self)
        g.solved_packages = packages
        g.solved = True


class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        FACTORY_REPOS = "SUSE:SLE-15:GA/standard"
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-r', '--repositories', dest='repostr', metavar='REPOS',
                          help='repositories to process (comma seperated list - default: %s)' % FACTORY_REPOS,
                          default=FACTORY_REPOS)
        parser.add_option('-i', '--input-dir', dest='input_dir', metavar='DIR',
                          help='input directory', default='.')
        parser.add_option('-o', '--output-dir', dest='output_dir', metavar='DIR',
                          help='input directory', default='.')
        return parser

    def setup_tool(self):
        tool = PkgListGen(self.options.repostr)
        tool.input_dir = self.options.input_dir
        tool.output_dir = self.options.output_dir
        return tool

    def do_list(self, subcmd, opts):
        """${cmd_name}: list all groups

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()

        for name in sorted(self.tool.groups.keys()):
            print name

    # to be called only once to bootstrap
    def do_dump_supportstatus(self, subcmd, opts):
        """${cmd_name}: dump supportstatus of input files

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()
        self.tool._dump_supportstatus()

    def do_list_products(self, subcmd, opts):
        """${cmd_name}: list all products

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.list_products()

    def do_update(self, subcmd, opts):
        """${cmd_name}: Solve groups

        ${cmd_usage}
        ${cmd_option_list}
        """

        # only there to parse the repos
        tool = PkgListGen(self.options.repostr)
        bs_mirrorfull = os.path.join(os.path.dirname(__file__), 'bs_mirrorfull')
        for prp in tool.repos:
            project = prp['project']
            repo = prp['repo']
            for arch in ARCHITECTURES:
                d = os.path.join(
                    CACHEDIR, 'repo-{}-{}-{}'.format(project, repo, arch))
                logger.debug('updating %s', d)
                subprocess.call(
                    [bs_mirrorfull, '{}/build/{}/{}/{}'.format(APIURL, project, repo, arch), d])
                files = [os.path.join(d, f)
                         for f in os.listdir(d) if f.endswith('.rpm')]
                fh = open(d + '.solv', 'w')
                p = subprocess.Popen(
                    ['rpms2solv', '-m', '-', '-0'], stdin=subprocess.PIPE, stdout=fh)
                p.communicate('\0'.join(files))
                p.wait()
                fh.close()

    def do_solve(self, subcmd, opts):
        """${cmd_name}: Solve groups

        ${cmd_usage}
        ${cmd_option_list}
        """

        output = self.tool.load_all_groups()
        if not output:
            return

        modules = []
        # the yml parser makes an array out of everything, so
        # we loop a bit more than what we support
        for group in output:
            groupname = group.keys()[0]
            settings = group[groupname]
            includes = settings.get('includes', [])
            excludes = settings.get('excludes', [])
            self.tool.solve_module(groupname, includes, excludes)
            modules.append(self.tool.groups[groupname])

        for module in modules:
            module.collect_devel_packages(modules)
            module.filter_duplicated_recommends(modules)

        self.tool._collect_unsorted_packages()
        self.tool._write_all_groups()


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
