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
from __future__ import print_function

import copy
from lxml import etree as ET
from collections import namedtuple
import sys
import cmdln
import logging
import urllib2
import filecmp
from osc.core import checkout_package
from osc.core import http_GET
from osc.core import makeurl
from osc.core import Package
from osc.core import show_results_meta
from osc.core import undelete_package
from osc import conf
from osclib.conf import Config, str2bool
from osclib.stagingapi import StagingAPI
from osclib.util import project_list_family
from osclib.util import project_list_family_prior
from xdg.BaseDirectory import save_cache_path
import glob
import hashlib
import io
import solv
from pprint import pprint, pformat
import os
import os.path
import subprocess
import re
import yaml
import requests
import urlparse
import gzip
import tempfile
import traceback
import random
import shutil
import string

import ToolBase

# share header cache with repochecker
from osclib.memoize import CACHEDIR

logger = logging.getLogger()

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
ARCHITECTURES = ['x86_64', 'ppc64le', 's390x', 'aarch64']
DEFAULT_REPOS = ("openSUSE:Factory/standard")
PRODUCT_SERVICE = '/usr/lib/obs/service/create_single_product'


class Group(object):

    def __init__(self, name, pkglist):
        self.name = name
        self.safe_name = re.sub(r'\W', '_', name.lower())
        self.pkglist = pkglist
        self.architectures = pkglist.architectures
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

        self.comment = ' ### AUTOMATICALLY GENERATED, DO NOT EDIT ### '
        self.srcpkgs = None
        self.develpkgs = dict()
        self.silents = set()
        self.ignored = set()
        # special feature for SLE. Patterns are marked for expansion
        # of recommended packages, all others aren't. Only works
        # with recommends on actual package names, not virtual
        # provides.
        self.expand_recommended = set()

        pkglist.groups[self.safe_name] = self

    def _add_to_packages(self, package, arch=None):
        archs = self.architectures
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
                arch = None
                if rel == 'locked':
                    self.locked.add(name)
                    continue
                elif rel == 'silent':
                    self.silents.add(name)
                elif rel == 'recommended':
                    self.expand_recommended.add(name)
                else:
                    arch = rel

                self._add_to_packages(name, arch)

    def _verify_solved(self):
        if not self.solved:
            raise Exception('group {} not solved'.format(self.name))

    def inherit(self, group):
        for arch in self.architectures:
            self.packages[arch] += group.packages[arch]

        self.locked.update(group.locked)
        self.silents.update(group.silents)
        self.expand_recommended.update(group.expand_recommended)

    # do not repeat packages
    def ignore(self, without):
        for arch in ['*'] + self.architectures:
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
        for g in without.ignored:
            self.ignore(g)
        self.ignored.add(without)

    def solve(self, ignore_recommended=False, include_suggested=False):
        """ base: list of base groups or None """

        solved = dict()
        for arch in self.architectures:
            solved[arch] = dict()

        self.srcpkgs = dict()
        self.recommends = dict()
        self.suggested = dict()
        for arch in self.architectures:
            pool = self.pkglist._prepare_pool(arch)
            # pool.set_debuglevel(10)
            suggested = []

            # packages resulting from explicit recommended expansion
            extra = []

            def solve_one_package(n, group):
                jobs = list(self.pkglist.lockjobs[arch])
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    logger.debug('{}.{}: package {} not found'.format(self.name, arch, n))
                    self.not_found.setdefault(n, set()).add(arch)
                    return
                else:
                    if n in self.expand_recommended:
                        for s in sel.solvables():
                            for dep in s.lookup_deparray(solv.SOLVABLE_RECOMMENDS):
                                # only add recommends that exist as packages
                                rec = pool.select(dep.str(), solv.Selection.SELECTION_NAME)
                                if not rec.isempty():
                                    extra.append([dep.str(), group + ":recommended:" + n])

                    jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

                locked = self.locked | self.pkglist.unwanted
                for l in locked:
                    sel = pool.select(str(l), solv.Selection.SELECTION_NAME)
                    if sel.isempty():
                        # if we can't find it, it probably is not as important
                        logger.debug('{}.{}: locked package {} not found'.format(self.name, arch, l))
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
                        msg = 'unresolvable: %s.%s: %s', self.name, arch, problem
                        if self.pkglist.ignore_broken:
                            logger.debug(msg)
                        else:
                            logger.debug(msg)
                        self.unresolvable[arch][n] = str(problem)
                    return

                if hasattr(solver, 'get_recommended'):
                    for s in solver.get_recommended():
                        if s.name in locked:
                            continue
                        self.recommends.setdefault(s.name, group + ':' + n)
                    for s in solver.get_suggested():
                        suggested.append([s.name, group + ':suggested:' + n])
                        self.suggested.setdefault(s.name, group + ':' + n)
                else:
                    logger.warn('newer libsolv needed for recommends!')

                trans = solver.transaction()
                if trans.isempty():
                    logger.error('%s.%s: nothing to do', self.name, arch)
                    return

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
                    self.srcpkgs[src] = group + ':' + s.name

            for n, group in self.packages[arch]:
                solve_one_package(n, group)

            if include_suggested:
                seen = set()
                while suggested:
                    n, group = suggested.pop()
                    if n in seen:
                        continue
                    seen.add(n)
                    solve_one_package(n, group)

        common = None
        # compute common packages across all architectures
        for arch in self.architectures:
            if common is None:
                common = set(solved[arch].keys())
                continue
            common &= set(solved[arch].keys())

        if common is None:
            common = set()

        # reduce arch specific set by common ones
        solved['*'] = dict()
        for arch in self.architectures:
            for p in common:
                solved['*'][p] = solved[arch].pop(p)

        self.solved_packages = solved
        self.solved = True

    def check_dups(self, modules, overlap):
        if not overlap:
            return
        packages = set(self.solved_packages['*'])
        for arch in self.architectures:
            packages.update(self.solved_packages[arch])
        for m in modules:
            # do not check with ourselves and only once for the rest
            if m.name <= self.name:
                continue
            if self.name in m.conflicts or m.name in self.conflicts:
                continue
            mp = set(m.solved_packages['*'])
            for arch in self.architectures:
                mp.update(m.solved_packages[arch])
            if len(packages & mp):
                overlap.comment += '\n overlapping between ' + self.name + ' and ' + m.name
                for p in sorted(packages & mp):
                    overlap.comment += '\n  - ' + p
                    overlap._add_to_packages(p)

    def collect_devel_packages(self):
        for arch in self.architectures:
            pool = self.pkglist._prepare_pool(arch)
            sel = pool.Selection()
            for s in pool.solvables_iter():
                if s.name.endswith('-devel'):
                    # don't ask me why, but that's how it seems to work
                    if s.lookup_void(solv.SOLVABLE_SOURCENAME):
                        src = s.name
                    else:
                        src = s.lookup_str(solv.SOLVABLE_SOURCENAME)

                    if src in self.srcpkgs.keys():
                        self.develpkgs[s.name] = self.srcpkgs[src]

    def _filter_already_selected(self, modules, pkgdict):
        # erase our own - so we don't filter our own
        for p in pkgdict.keys():
            already_present = False
            for m in modules:
                for arch in ['*'] + self.architectures:
                    already_present = already_present or (p in m.solved_packages[arch])
            if already_present:
                del pkgdict[p]

    def filter_already_selected(self, modules):
        self._filter_already_selected(modules, self.recommends)
        self._filter_already_selected(modules, self.suggested)

    def toxml(self, arch, ignore_broken=False, comment=None):
        packages = self.solved_packages[arch]

        name = self.name
        if arch != '*':
            name += '.' + arch

        root = ET.Element('group', {'name': name})
        if comment:
            c = ET.Comment(comment)
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
                msg = ' {} not found on {}'.format(name, ','.join(sorted(missing[name])))
                if ignore_broken:
                    c = ET.Comment(msg)
                    packagelist.append(c)
                    continue
                name = msg
            if name in unresolvable:
                msg = ' {} uninstallable: {}'.format(name, unresolvable[name])
                if ignore_broken:
                    c = ET.Comment(msg)
                    packagelist.append(c)
                    continue
                else:
                    logger.error(msg)
                    name = msg
            status = self.pkglist.supportstatus(name)
            attrs = {'name': name}
            if status is not None:
                attrs['supportstatus'] = status
            p = ET.SubElement(packagelist, 'package', attrs)
            if name in packages:
                c = ET.Comment(' reason: {} '.format(packages[name]))
                packagelist.append(c)

        return root

    def dump(self):
        pprint({'name': self.name, 'missing': self.missing, 'packages': self.packages,
                'solved': self.solved_packages, 'silents': self.silents})
        return
        archs = ['*'] + self.architectures
        for arch in archs:
            x = self.toxml(arch)
            print(ET.tostring(x, pretty_print=True))


class PkgListGen(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.repos = DEFAULT_REPOS
        # package -> supportatus
        self.packages = dict()
        self.default_support_status = 'l3'
        self.groups = dict()
        self._supportstatus = None
        self.input_dir = '.'
        self.output_dir = '.'
        self.lockjobs = dict()
        self.ignore_broken = False
        self.ignore_recommended = False
        self.include_suggested = False
        self.unwanted = set()
        self.output = None
        self.locales = set()

    def _dump_supportstatus(self):
        for name in self.packages.keys():
            for status in self.packages[name]:
                if status == self.default_support_status:
                    continue
                for group in self.packages[name][status]:
                    print(name, status)

    def _load_supportstatus(self):
        # XXX
        fn = os.path.join(self.input_dir, 'supportstatus.txt')
        self._supportstatus = dict()
        if os.path.exists(fn):
            with open(fn, 'r') as fh:
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
        unwanted = None
        with open(fn, 'r') as fh:
            logger.debug("reading %s", fn)
            for groupname, group in yaml.safe_load(fh).items():
                if groupname == 'OUTPUT':
                    output = group
                    continue
                if groupname == 'UNWANTED':
                    unwanted = set(group)
                    continue
                g = Group(groupname, self)
                g.parse_yml(group)
        return output, unwanted

    def load_all_groups(self):
        for fn in glob.glob(os.path.join(self.input_dir, 'group*.yml')):
            o, u = self._load_group_file(fn)
            if o:
                if self.output is not None:
                    raise Exception('OUTPUT defined multiple times')
                self.output = o
            if u:
                self.unwanted |= u

    def _write_all_groups(self):
        self._check_supplements()
        archs = ['*'] + self.architectures
        for name in self.groups:
            group = self.groups[name]
            if not group.solved:
                continue
            fn = '{}.group'.format(group.name)
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                comment = group.comment
                for arch in archs:
                    x = group.toxml(arch, self.ignore_broken, comment)
                    # only comment first time
                    comment = None
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
        g.solve(self.ignore_recommended, self.include_suggested)
        for e in excludes:
            g.ignore(self.groups[e])

    def _check_supplements(self):
        tocheck = set()
        for arch in self.architectures:
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
            project, reponame = prp.split('/')
            repo = pool.add_repo(project)
            s = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, reponame, arch))
            r = repo.add_solv(s)
            if not r:
                raise Exception("failed to add repo {}/{}/{}. Need to run update first?".format(project, reponame, arch))
            for solvable in repo.solvables_iter():
                if solvable.name in solvables:
                    self.lockjobs[arch].append(pool.Job(solv.Job.SOLVER_SOLVABLE | solv.Job.SOLVER_LOCK, solvable.id))
                solvables.add(solvable.name)

        pool.addfileprovides()
        pool.createwhatprovides()

        # https://github.com/openSUSE/libsolv/issues/231
        if hasattr(pool, 'set_namespaceproviders'):
            for l in self.locales:
                pool.set_namespaceproviders(solv.NAMESPACE_LANGUAGE, pool.Dep(l), True)
        else:
            logger.warn('libsolv missing set_namespaceproviders()')

        return pool

    # parse file and merge all groups
    def _parse_unneeded(self, filename):
        filename = os.path.join(self.input_dir, filename)
        if not os.path.isfile(filename):
            return set()
        fh = open(filename, 'r')
        logger.debug("reading %s", filename)
        result = set()
        for groupname, group in yaml.safe_load(fh).items():
            result.update(group)
        return result

    def _collect_unsorted_packages(self, modules):
        uneeded_regexps = [re.compile(r)
                           for r in self._parse_unneeded('unneeded.yml')]

        packages = dict()
        for arch in self.architectures:
            pool = self._prepare_pool(arch)
            sel = pool.Selection()
            archpacks = [s.name for s in pool.solvables_iter()]
            for r in uneeded_regexps:
                archpacks = [p for p in archpacks if not r.match(p)]

            # convert to set
            archpacks = set(archpacks) - self.unwanted
            for g in modules:
                for a in ('*', arch):
                    archpacks -= set(g.solved_packages[a].keys())
            for package in archpacks:
                packages.setdefault(package, []).append(arch)

        with open(os.path.join(self.output_dir, 'unsorted.yml'), 'w') as fh:
            fh.write("unsorted:\n")
            for p in sorted(packages.keys()):
                fh.write("  - ")
                fh.write(p)
                if len(packages[p]) != len(self.architectures):
                    fh.write(": [")
                    fh.write(','.join(sorted(packages[p])))
                    fh.write("]")
                    reason = self._find_reason(p, modules)
                    if reason:
                        fh.write(' # ' + reason)
                fh.write(" \n")

    # give a hint if the package is related to a group
    def _find_reason(self, package, modules):
        # go through the modules multiple times to find the "best"
        for g in modules:
            if package in g.recommends:
                return 'recommended by ' + g.recommends[package]
        for g in modules:
            if package in g.suggested:
                return 'suggested by ' + g.suggested[package]
        for g in modules:
            if package in g.develpkgs:
                return 'devel package of ' + g.develpkgs[package]
        return None


class CommandLineInterface(ToolBase.CommandLineInterface):
    SCOPES = ['all', 'target', 'rings', 'staging', 'ports']

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-r', '--repositories', dest='repos', metavar='REPOS', action='append',
                          help='repositories to process (%s)' % DEFAULT_REPOS)
        parser.add_option('-i', '--input-dir', dest='input_dir', metavar='DIR',
                          help='input directory', default='.')
        parser.add_option('-o', '--output-dir', dest='output_dir', metavar='DIR',
                          help='input directory', default='.')
        parser.add_option('-a', '--architecture', dest='architectures', metavar='ARCH',
                          help='architecure', action='append')
        parser.add_option('--default-support-status', dest='default_support_status', metavar='STATUS',
                          help='default support status', default=None)
        return parser

    def setup_tool(self):
        tool = PkgListGen()
        tool.input_dir = self.options.input_dir
        tool.output_dir = self.options.output_dir
        tool.default_support_status = self.options.default_support_status
        if self.options.architectures:
            tool.architectures = self.options.architectures
        else:
            tool.architectures = ARCHITECTURES
        if self.options.repos:
            repos = []
            for r in self.options.repos:
                # handle comas as well, easier for shell script for now
                if ',' in r:
                    repos += r.split(',')
                else:
                    repos.append(r)
            tool.repos = repos
        return tool

    def do_list(self, subcmd, opts):
        """${cmd_name}: list all groups

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()

        for name in sorted(self.tool.groups.keys()):
            print(name)

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
        bs_mirrorfull = os.path.join(SCRIPT_PATH, 'bs_mirrorfull')
        global_update = False
        for prp in self.tool.repos:
            project, repo = prp.split('/')
            for arch in self.tool.architectures:
                # TODO: refactor to common function with repo_checker.py
                d = os.path.join(CACHEDIR, project, repo, arch)
                if not os.path.exists(d):
                    os.makedirs(d)

                logger.debug('updating %s', d)
                args = [bs_mirrorfull]
                args.append('--nodebug')
                args.append('{}/public/build/{}/{}/{}'.format(self.tool.apiurl, project, repo, arch))
                args.append(d)
                p = subprocess.Popen(args, stdout=subprocess.PIPE)
                repo_update = False
                for line in p.stdout:
                    logger.info(line.rstrip())
                    global_update = True
                    repo_update = True

                solv_file = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
                if os.path.exists(solv_file) and not repo_update:
                    continue
                files = [os.path.join(d, f)
                         for f in os.listdir(d) if f.endswith('.rpm')]
                fh = open(solv_file, 'w')
                p = subprocess.Popen(
                    ['rpms2solv', '-m', '-', '-0'], stdin=subprocess.PIPE, stdout=fh)
                p.communicate('\0'.join(files))
                p.wait()
                fh.close()
        return global_update

    def update_merge(self, nonfree):
        """Merge free and nonfree solv files or copy free to merged"""
        for prp in self.tool.repos:
            project, repo = prp.split('/')
            for arch in self.tool.architectures:
                solv_file = os.path.join(
                    CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
                solv_file_merged = os.path.join(
                    CACHEDIR, 'repo-{}-{}-{}.merged.solv'.format(project, repo, arch))

                if not nonfree:
                    shutil.copyfile(solv_file, solv_file_merged)
                    continue

                solv_file_nonfree = os.path.join(
                    CACHEDIR, 'repo-{}-{}-{}.solv'.format(nonfree, repo, arch))
                self.solv_merge(solv_file_merged, solv_file, solv_file_nonfree)

    def solv_merge(self, solv_merged, *solvs):
        solvs = list(solvs)  # From tuple.

        if os.path.exists(solv_merged):
            modified = map(os.path.getmtime, [solv_merged] + solvs)
            if max(modified) <= modified[0]:
                # The two inputs were modified before or at the same as merged.
                logger.debug('merge skipped for {}'.format(solv_merged))
                return

        with open(solv_merged, 'w') as handle:
            p = subprocess.Popen(['mergesolv'] + solvs, stdout=handle)
            p.communicate()

        if p.returncode:
            raise Exception('failed to create merged solv file')

    def do_create_sle_weakremovers(self, subcmd, opts, *prjs):
        for prj in prjs:
            logger.debug("processing %s", prj)
            self.options.repos = ['/'.join([prj, 'standard'])]
            self.postoptparse()
            opts.project = prj
            self.do_update('update', opts)

        drops = dict()
        for arch in self.tool.architectures:
            pool = solv.Pool()
            pool.setarch(arch)

            sysrepo = None
            for prp in prjs:
                fn = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(prp, 'standard', arch))
                r = pool.add_repo(prp)
                r.add_solv(fn)
                if not sysrepo:
                    sysrepo = r

            pool.createwhatprovides()

            for s in pool.solvables_iter():
                if s.repo == sysrepo or not (s.arch == 'noarch' or s.arch == arch):
                    continue
                haveit = False
                for s2 in pool.whatprovides(s.nameid):
                    if s2.repo == sysrepo and s.nameid == s2.nameid:
                        haveit = True
                if haveit:
                    continue
                nevr = pool.rel2id(s.nameid, s.evrid, solv.REL_EQ)
                for s2 in pool.whatmatchesdep(solv.SOLVABLE_OBSOLETES, nevr):
                    if s2.repo == sysrepo:
                        continue
                    haveit = True
                if haveit:
                    continue
                if s.name not in drops:
                    drops[s.name] = {'repo': s.repo.name, 'archs': []}
                if arch not in drops[s.name]['archs']:
                    drops[s.name]['archs'].append(arch)
        for prp in prjs:
            exclusives = dict()
            print('#', prp)
            for name in sorted(drops.keys()):
                if drops[name]['repo'] != prp:
                    continue
                if len(drops[name]['archs']) == len(self.tool.architectures):
                    print('Provides: weakremover({})'.format(name))
                else:
                    jarch = ' '.join(sorted(drops[name]['archs']))
                    exclusives.setdefault(jarch, []).append(name)
            for arch in sorted(exclusives.keys()):
                print('%ifarch {}'.format(arch))
                for name in sorted(exclusives[arch]):
                    print('Provides: weakremover({})'.format(name))
                print('%endif')

    def do_create_droplist(self, subcmd, opts, *oldsolv):
        """${cmd_name}: generate list of obsolete packages

        The globally specified repositories are taken as the current
        package set. All solv files specified on the command line
        are old versions of those repos.

        The command outputs all package names that are no longer
        contained in or provided by the current repos.

        ${cmd_usage}
        ${cmd_option_list}
        """

        drops = dict()

        for arch in self.tool.architectures:

            for old in oldsolv:

                logger.debug("%s: processing %s", arch, old)

                pool = solv.Pool()
                pool.setarch(arch)

                for prp in self.tool.repos:
                    project, repo = prp.split('/')
                    fn = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
                    r = pool.add_repo(prp)
                    r.add_solv(fn)

                sysrepo = pool.add_repo(os.path.basename(old).replace('.merged.solv', ''))
                sysrepo.add_solv(old)

                pool.createwhatprovides()

                for s in sysrepo.solvables:
                    haveit = False
                    for s2 in pool.whatprovides(s.nameid):
                        if s2.repo == sysrepo or s.nameid != s2.nameid:
                            continue
                        haveit = True
                    if haveit:
                        continue
                    nevr = pool.rel2id(s.nameid, s.evrid, solv.REL_EQ)
                    for s2 in pool.whatmatchesdep(solv.SOLVABLE_OBSOLETES, nevr):
                        if s2.repo == sysrepo:
                            continue
                        haveit = True
                    if haveit:
                        continue
                    if s.name not in drops:
                        drops[s.name] = sysrepo.name

                # mark it explicitly to avoid having 2 pools while GC is not run
                del pool

        ofh = sys.stdout
        if self.options.output_dir:
            name = os.path.join(self.options.output_dir, 'obsoletepackages.inc')
            ofh = open(name, 'w')

        for reponame in sorted(set(drops.values())):
            print("<!-- %s -->" % reponame, file=ofh)
            for p in sorted(drops):
                if drops[p] != reponame:
                    continue
                print("  <obsoletepackage>%s</obsoletepackage>" % p, file=ofh)

    @cmdln.option('--overwrite', action='store_true', help='overwrite if output file exists')
    def do_dump_solv(self, subcmd, opts, baseurl):
        """${cmd_name}: fetch repomd and dump solv

        Dumps solv from published repository. Use solve to generate from
        pre-published repository.

        If an output directory is specified, a file named according
        to the build is created there. Otherwise the solv file is
        dumped to stdout.

        ${cmd_usage}
        ${cmd_option_list}
        """

        name = None
        ofh = sys.stdout
        if self.options.output_dir:
            build, repo_style = self.dump_solv_build(baseurl)
            name = os.path.join(self.options.output_dir, '{}.solv'.format(build))
            # For update repo name never changes so always update.
            if not opts.overwrite and repo_style != 'update' and os.path.exists(name):
                logger.info("%s exists", name)
                return name

        pool = solv.Pool()
        pool.setarch()

        repo = pool.add_repo(''.join(random.choice(string.letters) for _ in range(5)))
        path_prefix = 'suse/' if name and repo_style == 'build' else ''
        url = urlparse.urljoin(baseurl, path_prefix + 'repodata/repomd.xml')
        repomd = requests.get(url)
        ns = {'r': 'http://linux.duke.edu/metadata/repo'}
        root = ET.fromstring(repomd.content)
        primary_element = root.find('.//r:data[@type="primary"]', ns)
        location = primary_element.find('r:location', ns).get('href')
        sha256_expected = primary_element.find('r:checksum[@type="sha256"]', ns).text

        # No build information in update repo to use repomd checksum in name.
        if repo_style == 'update':
            name = os.path.join(self.options.output_dir, '{}::{}.solv'.format(build, sha256_expected))
            if not opts.overwrite and os.path.exists(name):
                logger.info("%s exists", name)
                return name

            # Only consider latest update repo so remove old versions.
            # Pre-release builds only make sense for non-update repos and once
            # releases then only relevant for next product which does not
            # consider pre-release from previous version.
            for old_solv in glob.glob(os.path.join(self.options.output_dir, '{}::*.solv'.format(build))):
                os.remove(old_solv)

        f = tempfile.TemporaryFile()
        f.write(repomd.content)
        f.flush()
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        repo.add_repomdxml(f, 0)
        url = urlparse.urljoin(baseurl, path_prefix + location)
        with requests.get(url, stream=True) as primary:
            sha256 = hashlib.sha256(primary.content).hexdigest()
            if sha256 != sha256_expected:
                raise Exception('checksums do not match {} != {}'.format(sha256, sha256_expected))

            content = gzip.GzipFile(fileobj=io.BytesIO(primary.content))
            os.lseek(f.fileno(), 0, os.SEEK_SET)
            f.write(content.read())
            f.flush()
            os.lseek(f.fileno(), 0, os.SEEK_SET)
            repo.add_rpmmd(f, None, 0)
            repo.create_stubs()

            ofh = open(name + '.new', 'w')
            repo.write(ofh)

        if name is not None:
            # Only update file if overwrite or different.
            ofh.flush()  # Ensure entirely written before comparing.
            if not opts.overwrite and os.path.exists(name) and filecmp.cmp(name + '.new', name, shallow=False):
                logger.debug('file identical, skip dumping')
                os.remove(name + '.new')
            else:
                os.rename(name + '.new', name)
            return name

    def dump_solv_build(self, baseurl):
        """Determine repo format and build string from remote repository."""
        if 'update' in baseurl:
            # Could look at .repo file or repomd.xml, but larger change.
            return 'update-' + os.path.basename(os.path.normpath(baseurl)), 'update'

        url = urlparse.urljoin(baseurl, 'media.1/media')
        with requests.get(url) as media:
            for i, line in enumerate(media.iter_lines()):
                if i != 1:
                    continue
                name = line

        if name is not None and '-Build' in name:
            return name, 'media'

        url = urlparse.urljoin(baseurl, 'media.1/build')
        with requests.get(url) as build:
            name = build.content.strip()

        if name is not None and '-Build' in name:
            return name, 'build'

        raise Exception('media.1/{media,build} includes no build number')

    @cmdln.option('--ignore-unresolvable', action='store_true', help='ignore unresolvable and missing packges')
    @cmdln.option('--ignore-recommended', action='store_true', help='do not include recommended packages automatically')
    @cmdln.option('--include-suggested', action='store_true', help='include suggested packges also')
    @cmdln.option('--locale', action='append', help='locales to inclues')
    @cmdln.option('--locales-from', metavar='FILE', help='get supported locales from product file FILE')
    def do_solve(self, subcmd, opts):
        """${cmd_name}: Solve groups

        Generates solv from pre-published repository contained in local cache.
        Use dump_solv to extract solv from published repository.

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()
        if not self.tool.output:
            logger.error('OUTPUT not defined')
            return

        if opts.ignore_unresolvable:
            self.tool.ignore_broken = True
        if opts.ignore_recommended:
            self.tool.ignore_recommended = True
        if opts.include_suggested:
            if opts.ignore_recommended:
                raise cmdln.CmdlnUserError("--ignore-recommended and --include-suggested don't work together")
            self.tool.include_suggested = True
        if opts.locale:
            for l in opts.locale:
                self.tool.locales |= set(l.split(','))
        if opts.locales_from:
            with open(os.path.join(self.tool.input_dir, opts.locales_from), 'r') as fh:
                root = ET.parse(fh).getroot()
                self.tool.locales |= set([lang.text for lang in root.findall(".//linguas/language")])

        modules = []
        # the yml parser makes an array out of everything, so
        # we loop a bit more than what we support
        for group in self.tool.output:
            groupname = group.keys()[0]
            settings = group[groupname]
            includes = settings.get('includes', [])
            excludes = settings.get('excludes', [])
            self.tool.solve_module(groupname, includes, excludes)
            g = self.tool.groups[groupname]
            g.conflicts = settings.get('conflicts', [])
            modules.append(g)

        # not defined for openSUSE
        overlap = self.tool.groups.get('overlap')
        for module in modules:
            module.check_dups(modules, overlap)
            module.collect_devel_packages()
            module.filter_already_selected(modules)

        if overlap:
            ignores = [x.name for x in overlap.ignored]
            self.tool.solve_module(overlap.name, [], ignores)
            overlapped = set(overlap.solved_packages['*'])
            for arch in overlap.architectures:
                overlapped |= set(overlap.solved_packages[arch])
            for module in modules:
                if module.name == 'overlap' or module in overlap.ignored:
                    continue
                for arch in ['*'] + module.architectures:
                    for p in overlapped:
                        module.solved_packages[arch].pop(p, None)

        self.tool._collect_unsorted_packages(modules)
        self.tool._write_all_groups()

    @cmdln.option('-f', '--force', action='store_true', help='continue even if build is in progress')
    @cmdln.option('-p', '--project', help='target project')
    @cmdln.option('-s', '--scope', default='all', help='scope on which to operate ({})'.format(', '.join(SCOPES)))
    def do_update_and_solve(self, subcmd, opts):
        """${cmd_name}: update and solve for given scope

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.error_occured = False

        if not opts.project:
            raise ValueError('project is required')
        if opts.scope not in self.SCOPES:
            raise ValueError('scope must be one of: {}'.format(', '.join(self.SCOPES)))

        if opts.scope == 'all':
            for scope in self.SCOPES[1:]:
                opts.scope = scope
                self.do_update_and_solve(subcmd, copy.deepcopy(opts))
            return self.error_occured

        # Store target project as opts.project will contain subprojects.
        target_project = opts.project

        config = Config(target_project)
        apiurl = conf.config['apiurl']
        api = StagingAPI(apiurl, target_project)
        config.apply_remote(api)

        target_config = conf.config[target_project]
        archs_key = 'pkglistgen-archs' if opts.scope != 'ports' else 'pkglistgen-archs-ports'
        if archs_key in target_config:
            self.options.architectures = target_config.get(archs_key).split(' ')
        main_repo = target_config['main-repo']

        if opts.scope == 'target':
            self.options.repos = ['/'.join([target_project, main_repo])]
            self.update_and_solve_target_wrapper(apiurl, target_project, target_config, main_repo, opts, drop_list=True)
            return self.error_occured
        elif opts.scope == 'ports':
            # TODO Continue supporting #1297, but should be abstracted.
            main_repo = 'ports'
            opts.project += ':Ports'
            target_project += ':Ports'
            self.options.repos = ['/'.join([opts.project, main_repo])]
            self.update_and_solve_target_wrapper(apiurl, target_project, target_config, main_repo, opts, drop_list=True)
            return self.error_occured
        elif opts.scope == 'rings':
            opts.project = api.rings[1]
            self.options.repos = [
                '/'.join([api.rings[1], main_repo]),
                '/'.join([api.rings[0], main_repo]),
            ]
            self.update_and_solve_target_wrapper(apiurl, target_project, target_config, main_repo, opts)

            opts.project = api.rings[2]
            self.options.repos.insert(0, '/'.join([api.rings[2], main_repo]))
            self.update_and_solve_target_wrapper(apiurl, target_project, target_config, main_repo, opts, skip_release=True)
            return self.error_occured
        elif opts.scope == 'staging':
            letters = api.get_staging_projects_short()
            for letter in letters:
                opts.project = api.prj_from_short(letter)
                self.options.repos = ['/'.join([opts.project, main_repo])]

                if not api.is_staging_bootstrapped(opts.project):
                    self.options.repos.append('/'.join([opts.project, 'bootstrap_copy']))

                # DVD project first since it depends on main.
                if api.item_exists(opts.project + ':DVD'):
                    opts_dvd = copy.deepcopy(opts)
                    opts_dvd.project += ':DVD'
                    self.options.repos.insert(0, '/'.join([opts_dvd.project, main_repo]))
                    self.update_and_solve_target_wrapper(apiurl, target_project, target_config, main_repo, opts_dvd, skip_release=True)
                    self.options.repos.pop(0)

                self.update_and_solve_target_wrapper(apiurl, target_project, target_config, main_repo, opts)
            return self.error_occured

    def update_and_solve_target_wrapper(self, *args, **kwargs):
        try:
            self.update_and_solve_target(*args, **kwargs)
        except Exception as e:
            # Print exception, but continue to prevent problems effecting one
            # project from killing the whole process. Downside being a common
            # error will be duplicated for each project. Common exceptions could
            # be excluded if a set list is determined, but that is likely not
            # practical.
            traceback.print_exc()
            self.error_occured = True

    def update_and_solve_target(self, apiurl, target_project, target_config, main_repo, opts,
                                skip_release=False, drop_list=False):
        print('[{}] {}/{}: update and solve'.format(opts.scope, opts.project, main_repo))

        group = target_config.get('pkglistgen-group', '000package-groups')
        product = target_config.get('pkglistgen-product', '000product')
        release = target_config.get('pkglistgen-release', '000release-packages')

        url = makeurl(apiurl, ['source', opts.project])
        packages = ET.parse(http_GET(url)).getroot()
        if packages.find('entry[@name="{}"]'.format(product)) is None:
            if not self.options.dry:
                undelete_package(apiurl, opts.project, product, 'revive')
            # TODO disable build.
            print('{} undeleted, skip dvd until next cycle'.format(product))
            return
        elif not opts.force:
            root = ET.fromstringlist(show_results_meta(apiurl, opts.project, product,
                                                       repository=[main_repo], multibuild=True))
            if len(root.xpath('result[@state="building"]')) or len(root.xpath('result[@state="dirty"]')):
                print('{}/{} build in progress'.format(opts.project, product))
                return

        checkout_list = [group, product]
        if not skip_release:
            checkout_list.append(release)

            if packages.find('entry[@name="{}"]'.format(release)) is None:
                if not self.options.dry:
                    undelete_package(apiurl, opts.project, product, 'revive')
                print('{} undeleted, skip dvd until next cycle'.format(release))
                return

        # Cache dir specific to hostname and project.
        host = urlparse.urlparse(apiurl).hostname
        cache_dir = save_cache_path('opensuse-packagelists', host, opts.project)

        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
        os.makedirs(cache_dir)

        group_dir = os.path.join(cache_dir, group)
        product_dir = os.path.join(cache_dir, product)
        release_dir = os.path.join(cache_dir, release)

        for package in checkout_list:
            checkout_package(apiurl, opts.project, package, expand_link=True, prj_dir=cache_dir)

        if not skip_release:
            self.unlink_all_except(release_dir)
        self.unlink_all_except(product_dir)
        self.copy_directory_contents(group_dir, product_dir,
                                     ['supportstatus.txt', 'groups.yml', 'package-groups.changes'])
        self.change_extension(product_dir, '.spec.in', '.spec')
        self.change_extension(product_dir, '.product.in', '.product')

        self.options.input_dir = group_dir
        self.options.output_dir = product_dir
        self.postoptparse()

        print('-> do_update')
        self.do_update('update', opts)

        nonfree = target_config.get('nonfree')
        if opts.scope != 'ports' and nonfree and drop_list:
            print('-> do_update nonfree')

            # Switch to nonfree repo (ugly, but that's how the code was setup).
            self.options.repos_ = self.options.repos
            self.options.repos = ['/'.join([nonfree, main_repo])]
            self.postoptparse()

            opts_nonfree = copy.deepcopy(opts)
            opts_nonfree.project = nonfree
            self.do_update('update', opts_nonfree)

            # Switch repo back to main target project.
            self.options.repos = self.options.repos_
            self.postoptparse()

        print('-> update_merge')
        self.update_merge(nonfree if drop_list else False)

        print('-> do_solve')
        opts.ignore_unresolvable = str2bool(target_config.get('pkglistgen-ignore-unresolvable'))
        opts.ignore_recommended = str2bool(target_config.get('pkglistgen-ignore-recommended'))
        opts.include_suggested = str2bool(target_config.get('pkglistgen-include-suggested'))
        opts.locale = target_config.get('pkglistgen-local')
        opts.locales_from = target_config.get('pkglistgen-locales-from')
        self.do_solve('solve', opts)

        if drop_list:
            # Ensure solv files from all releases in product family are updated.
            print('-> solv_cache_update')
            cache_dir_solv = save_cache_path('opensuse-packagelists', 'solv')
            family_last = target_config.get('pkglistgen-product-family-last')
            family_include = target_config.get('pkglistgen-product-family-include')
            solv_prior = self.solv_cache_update(
                apiurl, cache_dir_solv, target_project, family_last, family_include, opts)

            # Include pre-final release solv files for target project. These
            # files will only exist from previous runs.
            cache_dir_solv_current = os.path.join(cache_dir_solv, target_project)
            solv_prior.update(glob.glob(os.path.join(cache_dir_solv_current, '*.merged.solv')))
            for solv_file in solv_prior:
                logger.debug(solv_file.replace(cache_dir_solv, ''))

            print('-> do_create_droplist')
            # Reset to product after solv_cache_update().
            self.options.output_dir = product_dir
            self.do_create_droplist('create_droplist', opts, *solv_prior)

        delete_products = target_config.get('pkglistgen-delete-products', '').split(' ')
        self.unlink_list(product_dir, delete_products)

        print('-> product service')
        for product_file in glob.glob(os.path.join(product_dir, '*.product')):
            print(subprocess.check_output(
                [PRODUCT_SERVICE, product_file, product_dir, opts.project]))

        delete_kiwis = target_config.get('pkglistgen-delete-kiwis-{}'.format(opts.scope), '').split(' ')
        self.unlink_list(product_dir, delete_kiwis)
        if opts.scope == 'staging':
            self.strip_medium_from_staging(product_dir)

        spec_files = glob.glob(os.path.join(product_dir, '*.spec'))
        if skip_release:
            self.unlink_list(None, spec_files)
        else:
            self.move_list(spec_files, release_dir)

        self.multibuild_from_glob(product_dir, '*.kiwi')
        self.build_stub(product_dir, 'kiwi')
        self.commit_package(product_dir)

        if not skip_release:
            self.multibuild_from_glob(release_dir, '*.spec')
            self.build_stub(release_dir, 'spec')
            self.commit_package(release_dir)

    def solv_cache_update(self, apiurl, cache_dir_solv, target_project, family_last, family_include, opts):
        """Dump solv files (do_dump_solv) for all products in family."""
        prior = set()

        project_family = project_list_family_prior(
            apiurl, target_project, include_self=True, last=family_last)
        if family_include:
            # Include projects from a different family if desired.
            project_family.extend(project_list_family(apiurl, family_include))

        for project in project_family:
            config = Config(project)
            project_config = conf.config[project]

            baseurl = project_config.get('download-baseurl')
            baseurl_update = project_config.get('download-baseurl-update')
            if not baseurl:
                logger.warning('no baseurl configured for {}'.format(project))
                continue

            urls = [urlparse.urljoin(baseurl, 'repo/oss/')]
            if baseurl_update:
                urls.append(urlparse.urljoin(baseurl_update, 'oss/'))
            if project_config.get('nonfree'):
                urls.append(urlparse.urljoin(baseurl, 'repo/non-oss/'))
                if baseurl_update:
                    urls.append(urlparse.urljoin(baseurl_update, 'non-oss/'))

            names = []
            for url in urls:
                project_display = project
                if 'update' in url:
                    project_display += ':Update'
                print('-> do_dump_solv for {}/{}'.format(
                    project_display, os.path.basename(os.path.normpath(url))))
                logger.debug(url)

                self.options.output_dir = os.path.join(cache_dir_solv, project)
                if not os.path.exists(self.options.output_dir):
                    os.makedirs(self.options.output_dir)

                opts.overwrite = False
                names.append(self.do_dump_solv('dump_solv', opts, url))

            if not len(names):
                logger.warning('no solv files were dumped for {}'.format(project))
                continue

            # Merge nonfree solv with free solv or copy free solv as merged.
            merged = names[0].replace('.solv', '.merged.solv')
            if len(names) >= 2:
                self.solv_merge(merged, *names)
            else:
                shutil.copyfile(names[0], merged)
            prior.add(merged)

        return prior

    # staging projects don't need source and debug medium - and the glibc source
    # rpm conflicts between standard and bootstrap_copy repository causing the
    # product builder to fail
    def strip_medium_from_staging(self, path):
        medium = re.compile('name="(DEBUG|SOURCE)MEDIUM"')
        for name in glob.glob(os.path.join(path, '*.kiwi')):
            lines = open(name).readlines()
            lines = [l for l in lines if not medium.search(l)]
            open(name, 'w').writelines(lines)

    def move_list(self, file_list, destination):
        for name in file_list:
            os.rename(name, os.path.join(destination, os.path.basename(name)))

    def unlink_list(self, path, names):
        for name in names:
            if path is None:
                name_path = name
            else:
                name_path = os.path.join(path, name)

            if os.path.isfile(name_path):
                os.unlink(name_path)

    def unlink_all_except(self, path, ignore_list=['_service'], ignore_hidden=True):
        for name in os.listdir(path):
            if name in ignore_list or (ignore_hidden and name.startswith('.')):
                continue

            name_path = os.path.join(path, name)
            if os.path.isfile(name_path):
                os.unlink(name_path)

    def copy_directory_contents(self, source, destination, ignore_list=[]):
        for name in os.listdir(source):
            name_path = os.path.join(source, name)
            if name in ignore_list or not os.path.isfile(name_path):
                continue

            shutil.copy(name_path, os.path.join(destination, name))

    def change_extension(self, path, original, final):
        for name in glob.glob(os.path.join(path, '*{}'.format(original))):
            # Assumes the extension is only found at the end.
            os.rename(name, name.replace(original, final))

    def multibuild_from_glob(self, destination, pathname):
        root = ET.Element('multibuild')
        for name in sorted(glob.glob(os.path.join(destination, pathname))):
            package = ET.SubElement(root, 'package')
            package.text = os.path.splitext(os.path.basename(name))[0]

        with open(os.path.join(destination, '_multibuild'), 'w+b') as f:
            f.write(ET.tostring(root, pretty_print=True))

    def build_stub(self, destination, extension):
        f = file(os.path.join(destination, '.'.join(['stub', extension])), 'w+')
        f.write('# prevent building single {} files twice\n'.format(extension))
        f.write('Name: stub\n')
        f.write('Version: 0.0\n')
        f.close()

    def commit_package(self, path):
        package = Package(path)
        if self.options.dry:
            for i in package.get_diff():
                print(''.join(i))
        else:
            # No proper API function to perform the same operation.
            print(subprocess.check_output(
                ' '.join(['cd', path, '&&', 'osc', 'addremove']), shell=True))
            package.commit(msg='Automatic update', skip_local_service_run=True)


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
