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
        self.base = None
        self.missing = dict()
        for a in ('*', ) + ARCHITECTURES:
            self.packages[a] = set()
            self.missing[a] = set()

        self.srcpkgs = None
        self.silents = set()

        pkglist.groups[self.safe_name] = self

    def _verify_solved(self):
        if not self.solved:
            raise Exception('group {} not solved'.format(self.name))

    def merge_solved_group(self, group, without=None):
        group._verify_solved()
        self._verify_solved()

        for arch in ('*', ) + ARCHITECTURES:
            self.missing[arch] = self.missing[arch] | group.missing[arch]
            packages = self.packages[arch]
            packages = group.packages[arch] | packages
            self.packages[arch] = packages

            packages = self.solved_packages[arch]
            packages = group.solved_packages[arch] | packages
            self.solved_packages[arch] = packages

        # remove packages duplicated
        generic_packages = self.solved_packages['*']
        for arch in ARCHITECTURES:
            arch_packages = self.solved_packages[arch]
            self.solved_packages[arch] = arch_packages - generic_packages

        self.silents.update(group.silents)

    # do not repeat packages
    def ignore(self, without):
        for arch in ('*', ) + ARCHITECTURES:
            self.packages[arch] -= without.packages[arch]
            self.packages[arch] -= without.packages['*']
            self.solved_packages[arch] -= without.packages[arch]
            self.solved_packages[arch] -= without.solved_packages[arch]
            self.solved_packages[arch] -= without.packages['*']
            self.solved_packages[arch] -= without.solved_packages['*']

    def get_solved_packages_recursive(self, arch):
        self._verify_solved()

        solved = set(self.solved_packages['*'])
        if arch in self.solved_packages:
            solved |= self.solved_packages[arch]
        logger.debug(
            "{}.{} have {} packages".format(self.name, arch, len(solved)))
        if self.base:
            for b in self.base:
                solved |= b.get_solved_packages_recursive(arch)

        return solved

    def get_packages_recursive(self, arch):
        packages = set()
        if '*' in self.packages:
            packages.update(self.packages['*'])
        if arch in self.packages:
            packages.update(self.packages[arch])
        logger.debug(
            "{}.{} have {} packages".format(self.name, arch, len(packages)))
        if self.base:
            for b in self.base:
                packages |= b.get_packages_recursive(arch)

        return packages

    def solve(self, base=None, extra=None, without=None, ignore_recommended=True):
        """ base: list of base groups or None """

        if self.solved:
            return

        if isinstance(base, Group):
            base = [base]
        if not (base is None or isinstance(base, list) or isinstance(base, tuple)):
            raise Exception("base must be list but is {}".format(type(base)))
        if extra:
            if isinstance(extra, str):
                extra = set((extra))
            elif not (isinstance(extra, list) or isinstance(extra, tuple)):
                raise Exception(
                    "extra must be list but is {}".format(type(extra)))
            extra = set(extra)
        if without:
            if isinstance(without, str):
                without = set([without])
            elif not (isinstance(without, list) or isinstance(without, tuple)):
                raise Exception(
                    "without must be list but is {}".format(type(without)))
            without = set(without)

        solved = dict()
        missing = dict()
        for arch in ('*', ) + ARCHITECTURES:
            missing[arch] = set()
            solved[arch] = set()

        srcpkgs = set()
        for arch in ARCHITECTURES:
            pool = self.pkglist._prepare_pool(arch)
            # pool.set_debuglevel(10)

            jobs = []
            toinstall = self.packages['*'] | self.packages[arch]
            locked = set(self.locked)
            basepackages = set()
            basepackages_solved = set()
            if base:
                for b in base:
                    locked.update(b.locked)
                    logger.debug(
                        "{} adding packges from {}".format(self.name, b.name))
                    basepackages |= b.get_packages_recursive(arch)
                    basepackages_solved |= b.get_solved_packages_recursive(
                        arch)
                    self.silents.update(b.silents)
                self.base = list(base)
            if without:
                basepackages -= without
            toinstall |= basepackages
            if extra:
                toinstall.update(extra)
            for n in toinstall:
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    logger.error(
                        '{}.{}: package {} not found'.format(self.name, arch, n))
                    missing[arch].add(n)
                    if n in self.packages['*']:
                        self.packages['*'].remove(n)
                        for a in ARCHITECTURES:
                            self.packages[a].add(n)
                else:
                    jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

            for n in locked:
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    logger.warn(
                        '{}.{}: locked package {} not found'.format(self.name, arch, n))
                else:
                    jobs += sel.jobs(solv.Job.SOLVER_LOCK)

            solver = pool.Solver()
            if ignore_recommended:
                solver.set_flag(solver.SOLVER_FLAG_IGNORE_RECOMMENDED, 1)

            problems = solver.solve(jobs)
            if problems:
                for problem in problems:
                    # just ignore conflicts here
                    # if not ' conflicts with ' in str(problem):
                    logger.error(
                        'unresolvable: %s.%s: %s', self.name, arch, problem)
                    # logger.warning(problem)
                continue

            trans = solver.transaction()
            if trans.isempty():
                logger.error('%s.%s: nothing to do', self.name, arch)
                continue

            for s in trans.newsolvables():
                solved[arch].add(s.name)
                reason, rule = solver.describe_decision(s)
                if None:
                    print(self.name, s.name, reason, rule.info().problemstr())
                # don't ask me why, but that's how it seems to work
                if s.lookup_void(solv.SOLVABLE_SOURCENAME):
                    src = s.name
                else:
                    src = s.lookup_str(solv.SOLVABLE_SOURCENAME)
                srcpkgs.add(src)

            if basepackages_solved:
                solved[arch] -= basepackages_solved

            if extra:
                solved[arch] -= extra

        common = None
        missing_common = None
        # compute common packages across all architectures
        for arch in ARCHITECTURES:
            if common is None:
                common = set(solved[arch])
                continue
            common &= solved[arch]

        if common is None:
            common = set()

        for arch in missing.keys():
            if missing_common is None:
                missing_common = set(missing[arch])
                continue
            missing_common &= missing[arch]

        # reduce arch specific set by common ones
        for arch in solved.keys():
            solved[arch] -= common

        for arch in missing.keys():
            missing[arch] -= missing_common

        self.missing = missing
        if missing_common:
            self.missing['*'] = missing_common

        self.solved_packages = solved
        self.solved_packages['*'] = common

        self.solved = True
        self.srcpkgs = srcpkgs

    def architectures(self):
        return self.solved_packages.keys()

    def merge_missing(self):
        all_arch_missing = None
        for arch in ARCHITECTURES:
            if all_arch_missing is None:
                all_arch_missing = set(self.missing[arch])
            all_arch_missing &= self.missing[arch]
        for arch in ARCHITECTURES:
            self.missing[arch] -= all_arch_missing
	    self.packages[arch] -= all_arch_missing
        self.packages['*'] |= all_arch_missing
	self.missing['*'] |= all_arch_missing

    def toxml(self, arch):

        packages = None
        autodeps = None

        if arch in self.solved_packages:
            autodeps = self.solved_packages[arch]

        if arch in self.packages:
            packages = self.packages[arch]
            if autodeps:
                autodeps -= self.packages[arch]

        name = self.name
        if arch != '*':
            name += '.' + arch

        root = ET.Element('group', {'name': name})
        c = ET.Comment(' ### AUTOMATICALLY GENERATED, DO NOT EDIT ### ')
        root.append(c)

        if self.base:
            c = ET.Comment(' based on {} '.format(
                ', '.join([b.name for b in self.base])))
            root.append(c)

        if arch != '*':
            cond = ET.SubElement(root, 'conditional', {
                                 'name': 'only_{}'.format(arch)})
        packagelist = ET.SubElement(
            root, 'packagelist', {'relationship': 'recommends'})

        if packages:
            for name in sorted(packages):
                if name in self.silents:
                    continue
                if name in (self.missing[arch] | self.missing['*']):
                    c = ET.Comment(' missing {} '.format(name))
                    packagelist.append(c)
                else:
                    status = self.pkglist.supportstatus(name)
                    if status:
                        p = ET.SubElement(packagelist, 'package', {
                            'name': name,
                            'supportstatus': status
                        })

        if autodeps:
            c = ET.Comment(' automatic dependencies ')
            packagelist.append(c)

            for name in sorted(autodeps):
                if name in self.silents:
                    continue
                status = self.pkglist.supportstatus(name)
                if status:
                    p = ET.SubElement(packagelist, 'package', {
                        'name': name,
                        'supportstatus': self.pkglist.supportstatus(name)
                    })

        return root

    def dump(self):
        pprint({'name': self.name, 'missing': self.missing, 'packages': self.packages,
                'solved': self.solved_packages, 'silents': self.silents})
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

    # XXX: move to group class. just here to check supportstatus
    def _parse_group(self, groupname, packages):
        group = Group(groupname, self)
        for package in packages:
            if isinstance(package, dict):
                name = package.keys()[0]
                for rel in package[name]:
                    if rel == 'locked':
                        group.locked.add(name)
                    elif rel == 'silent':
                        group.packages.setdefault('*', set()).add(name)
                        group.silents.add(name)
                    else:
                        group.packages.setdefault(rel, set()).add(name)
            else:
                group.packages.setdefault('*', set()).add(package)

        return group

    def _load_group_file(self, fn):
        with open(fn, 'r') as fh:
            logger.debug("reading %s", fn)
            for groupname, group in yaml.safe_load(fh).items():
                g = self._parse_group(groupname, group)

    def load_all_groups(self):
        for fn in glob.glob(os.path.join(self.input_dir, 'group*.yml')):
            self._load_group_file(fn)

    def _write_all_groups(self):
        self._check_supplements()
        archs = ('*',) + ARCHITECTURES
        for name in self.groups:
            group = self.groups[name]
            group.merge_missing()
            fn = '{}.group'.format(group.name)
            if not group.solved:
                logger.error('{} not solved'.format(name))
                if os.path.exists(fn):
                    os.unlink(fn)
                continue
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                for arch in archs:
                    x = group.toxml(arch)
                    # fh.write(ET.tostring(x, pretty_print = True, doctype = '<?xml version="1.0" encoding="UTF-8"?>'))
                    fh.write(ET.tostring(x, pretty_print=True))

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

    def solve_group(self, name):
        self._load_all_groups()
        group = self.groups[name]
        group.solve()
        return group

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

        for prp in self.repos:
            project = prp['project']
            reponame = prp['repo']
            repo = pool.add_repo(project)
            s = os.path.join(
                CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, reponame, arch))
            r = repo.add_solv(s)
            if not r:
                raise Exception(
                    "failed to add repo {}/{}/{}. Need to run update first?".format(project, reponame, arch))

        pool.addfileprovides()
        pool.createwhatprovides()

        return pool

    def _collect_devel_packages(self):
        srcpkgs = set()
        for g in self.groups.values():
            if g.srcpkgs:
                srcpkgs.update(g.srcpkgs)

        develpkgs = dict()
        for arch in ARCHITECTURES:
            develpkgs[arch] = set()
            pool = self._prepare_pool(arch)
            sel = pool.Selection()
            for s in pool.solvables_iter():
                if s.name.endswith('-devel'):
                    # don't ask me why, but that's how it seems to work
                    if s.lookup_void(solv.SOLVABLE_SOURCENAME):
                        src = s.name
                    else:
                        src = s.lookup_str(solv.SOLVABLE_SOURCENAME)

                    if src in srcpkgs:
                        develpkgs[arch].add(s.name)

        common = None
        # compute common packages across all architectures
        for arch in develpkgs.keys():
            if common is None:
                common = set(develpkgs[arch])
                continue
            common &= develpkgs[arch]

        # reduce arch specific set by common ones
        for arch in develpkgs.keys():
            develpkgs[arch] -= common

        develpkgs['*'] = common

        g = Group('all-devel-pkgs', self)
        # XXX: would need to add to packages instead, then solve and
        # subtract all other groups
        g.solved_packages = develpkgs
        g.solved = True

    def _collect_unsorted_packages(self):

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
        bs_mirrorfull = os.path.join(
            os.path.dirname(__file__), 'bs_mirrorfull')
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

        self.tool.load_all_groups()

        self._solve()

#        sle_base.dump()

        self.tool._collect_devel_packages()
        self.tool._collect_unsorted_packages()
        self.tool._write_all_groups()

    def _solve(self):
        """ imlement this"""

        class G(object):
            True

        g = G()

        for group in self.tool.groups.values():
            setattr(g, group.safe_name, group)

        raise Exception('implement me in subclass')


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
