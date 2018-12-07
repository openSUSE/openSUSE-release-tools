from __future__ import print_function
import ToolBase
import glob
import logging
import os
import re
import solv
import subprocess
import yaml
from lxml import etree as ET

from osc.core import HTTPError
from osclib.core import repository_path_expand
from osclib.core import repository_arch_state
from osclib.cache_manager import CacheManager

from pkglistgen.group import Group

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
# share header cache with repochecker
CACHEDIR = CacheManager.directory('repository-meta')

class PkgListGen(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        # package -> supportatus
        self.packages = dict()
        self.groups = dict()
        self._supportstatus = None
        self.input_dir = '.'
        self.output_dir = '.'
        self.lockjobs = dict()
        self.ignore_broken = False
        self.unwanted = set()
        self.output = None
        self.locales = set()
        self.did_update = False
        self.logger = logging.getLogger(__name__)

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

    def supportstatus(self, package):
        if self._supportstatus is None:
            self._load_supportstatus()

        return self._supportstatus.get(package)

    def _load_group_file(self, fn):
        output = None
        unwanted = None
        with open(fn, 'r') as fh:
            self.logger.debug("reading %s", fn)
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
        summary = dict()
        archs = ['*'] + self.architectures
        for name in self.groups:
            group = self.groups[name]
            if not group.solved:
                continue
            summary[name] = group.summary()
            fn = '{}.group'.format(group.name)
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                comment = group.comment
                for arch in archs:
                    x = group.toxml(arch, self.ignore_broken, comment)
                    # only comment first time
                    comment = None
                    x = ET.tostring(x, pretty_print=True)
                    x = re.sub(r'\s*<!-- reason:', ' <!-- reason:', x)
                    # fh.write(ET.tostring(x, pretty_print = True, doctype = '<?xml version="1.0" encoding="UTF-8"?>'))
                    fh.write(x)
        return summary

    def solve_module(self, groupname, includes, excludes, use_recommends):
        g = self.groups[groupname]
        for i in includes:
            g.inherit(self.groups[i])
        g.solve(use_recommends)
        for e in excludes:
            g.ignore(self.groups[e])

    def expand_repos(self, project, repo='standard'):
        return repository_path_expand(self.apiurl, project, repo)

    def _check_supplements(self):
        tocheck = set()
        tocheck_locales = set()
        for arch in self.filtered_architectures:
            pool = self._prepare_pool(arch)
            sel = pool.Selection()
            for s in pool.solvables_iter():
                sel.add_raw(solv.Job.SOLVER_SOLVABLE, s.id)

            for s in sel.solvables():
                for dep in s.lookup_deparray(solv.SOLVABLE_SUPPLEMENTS):
                    for d in dep.str().split(' '):
                        if d.startswith('namespace:modalias') or d.startswith('namespace:filesystem'):
                            tocheck.add(s.name)

            for l in self.locales:
                i = pool.str2id('locale({})'.format(l))
                for s in pool.whatprovides(i):
                    tocheck_locales.add(s.name)

        all_grouped = set()
        for g in self.groups.values():
            if g.solved:
                for arch in g.solved_packages.keys():
                    if g.solved_packages[arch]:
                        all_grouped.update(g.solved_packages[arch])

        for p in tocheck - all_grouped:
            self.logger.warn('package %s has supplements but is not grouped', p)

        for p in tocheck_locales - all_grouped:
            self.logger.warn('package %s provides supported locale but is not grouped', p)

    def _prepare_pool(self, arch):
        pool = solv.Pool()
        pool.setarch(arch)

        self.lockjobs[arch] = []
        solvables = set()

        for project, reponame in self.repos:
            repo = pool.add_repo(project)
            s = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, reponame, arch))
            r = repo.add_solv(s)
            if not r:
                if not self.did_update:
                    raise Exception(
                        "failed to add repo {}/{}/{}. Need to run update first?".format(project, reponame, arch))
                continue
            for solvable in repo.solvables_iter():
                solvable.unset(solv.SOLVABLE_CONFLICTS)
                solvable.unset(solv.SOLVABLE_OBSOLETES)
                # only take the first solvable in the repo chain
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
            self.logger.warn('libsolv missing set_namespaceproviders()')

        return pool

    # parse file and merge all groups
    def _parse_unneeded(self, filename):
        filename = os.path.join(self.input_dir, filename)
        if not os.path.isfile(filename):
            return set()
        fh = open(filename, 'r')
        self.logger.debug("reading %s", filename)
        result = set()
        for groupname, group in yaml.safe_load(fh).items():
            result.update(group)
        return result

    # the unsorted group is special and will contain all the rest for
    # the FTP tree. We filter it with unneeded though to create a
    # unsorted.yml file for release manager review
    def _collect_unsorted_packages(self, modules, unsorted):
        uneeded_regexps = [re.compile(r)
                           for r in self._parse_unneeded('unneeded.yml')]

        packages = dict()
        if unsorted:
            unsorted.solved_packages = dict()
            unsorted.solved_packages['*'] = dict()

        for arch in self.filtered_architectures:
            pool = self._prepare_pool(arch)
            sel = pool.Selection()
            archpacks = [s.name for s in pool.solvables_iter()]

            # copy
            filtered = list(archpacks)
            for r in uneeded_regexps:
                filtered = [p for p in filtered if not r.match(p)]

            # convert to set
            filtered = set(filtered) - self.unwanted
            for g in modules:
                if unsorted and g == unsorted:
                    continue
                for a in ('*', arch):
                    filtered -= set(g.solved_packages[a].keys())
            for package in filtered:
                packages.setdefault(package, []).append(arch)

            if unsorted:
                archpacks = set(archpacks)
                unsorted.solved_packages[arch] = dict()
                for g in modules:
                    archpacks -= set(g.solved_packages[arch].keys())
                    archpacks -= set(g.solved_packages['*'].keys())
                unsorted.solved_packages[arch] = dict()
                for p in archpacks:
                    unsorted.solved_packages[arch][p] = None

        if unsorted:
            common = None
            for arch in self.filtered_architectures:
                if common is None:
                    common = set(unsorted.solved_packages[arch].keys())
                    continue
                common &= set(unsorted.solved_packages[arch].keys())
            for p in common:
                unsorted.solved_packages['*'][p] = None
                for arch in self.filtered_architectures:
                    del unsorted.solved_packages[arch][p]

        with open(os.path.join(self.output_dir, 'unsorted.yml'), 'w') as fh:
            fh.write("unsorted:\n")
            for p in sorted(packages.keys()):
                fh.write("  - ")
                fh.write(p)
                if len(packages[p]) != len(self.filtered_architectures):
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

    def update_repos(self, architectures):
        # only there to parse the repos
        bs_mirrorfull = os.path.join(SCRIPT_PATH, '..', 'bs_mirrorfull')
        global_update = False

        for project, repo in self.repos:
            for arch in architectures:
                # TODO: refactor to common function with repo_checker.py
                d = os.path.join(CACHEDIR, project, repo, arch)
                if not os.path.exists(d):
                    os.makedirs(d)

                try:
                    # Fetch state before mirroring in-case it changes during download.
                    state = repository_arch_state(self.apiurl, project, repo, arch)
                except HTTPError:
                    continue

                # Would be preferable to include hash in name, but cumbersome to handle without
                # reworking a fair bit since the state needs to be tracked.
                solv_file = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
                solv_file_hash = '{}::{}'.format(solv_file, state)
                if os.path.exists(solv_file) and os.path.exists(solv_file_hash):
                    # Solve file exists and hash unchanged, skip updating solv.
                    self.logger.debug('skipping solv generation for {} due to matching state {}'.format(
                        '/'.join([project, repo, arch]), state))
                    continue

                # Either hash changed or new, so remove any old hash files.
                self.unlink_list(None, glob.glob(solv_file + '::*'))
                global_update = True

                self.logger.debug('updating %s', d)
                args = [bs_mirrorfull]
                args.append('--nodebug')
                args.append('{}/public/build/{}/{}/{}'.format(self.apiurl, project, repo, arch))
                args.append(d)
                p = subprocess.Popen(args, stdout=subprocess.PIPE)
                for line in p.stdout:
                    self.logger.info(line.rstrip())

                files = [os.path.join(d, f)
                         for f in os.listdir(d) if f.endswith('.rpm')]
                fh = open(solv_file, 'w')
                p = subprocess.Popen(
                    ['rpms2solv', '-m', '-', '-0'], stdin=subprocess.PIPE, stdout=fh)
                p.communicate('\0'.join(files))
                p.wait()
                fh.close()

                # Create hash file now that solv creation is complete.
                open(solv_file_hash, 'a').close()
        self.did_update = True

        return global_update

    def unlink_list(self, path, names):
        for name in names:
            if path is None:
                name_path = name
            else:
                name_path = os.path.join(path, name)

            if os.path.isfile(name_path):
                os.unlink(name_path)

    def create_sle_weakremovers(self, target, oldprjs):
        self.repos = []
        for prj in list(oldprjs) + [target]:
            self.repos += self.expand_repos(prj, 'standard')

        #self.update_repos(self.architectures)

        drops = dict()
        for arch in self.architectures:
            pool = solv.Pool()
            pool.setarch(arch)

            sysrepo = None
            for project, repo in self.repos:
                self.logger.debug("processing %s/%s/%s", project, repo, arch)
                fn = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
                r = pool.add_repo('/'.join([project, repo]))
                r.add_solv(fn)
                if project == target and repo == 'standard':
                    sysrepo = r

            pool.createwhatprovides()

            for s in pool.solvables_iter():
                # we only want the old repos
                if s.repo == sysrepo: continue
                # ignore imported solvables. too dangerous
                if s.arch != 'noarch' and s.arch != arch:
                    continue
                haveit = False
                for s2 in pool.whatprovides(s.nameid):
                    if s2.repo == sysrepo and s.nameid == s2.nameid:
                        haveit = True
                if haveit:
                    continue
                haveit = False

                # check for already obsoleted packages
                nevr = pool.rel2id(s.nameid, s.evrid, solv.REL_EQ)
                for s2 in pool.whatmatchesdep(solv.SOLVABLE_OBSOLETES, nevr):
                    if s2.repo == sysrepo: continue
                    haveit = True
                if haveit:
                    continue
                drops.setdefault(s.name, {'repo': s.repo.name, 'archs': set()})
                drops[s.name]['archs'].add(arch)

        for project, repo in sorted(self.repos):
            exclusives = dict()
            print('#', project)
            for name in sorted(drops.keys()):
                #
                if drops[name]['repo'] != '{}/{}'.format(project, repo):
                    #print(drops[name]['repo'], '!=', '{}/{}'.format(project, repo))
                    continue
                if len(drops[name]['archs']) == len(self.architectures):
                    print('Provides: weakremover({})'.format(name))
                else:
                    jarch = ' '.join(sorted(drops[name]['archs']))
                    exclusives.setdefault(jarch, []).append(name)

            for arch in sorted(exclusives.keys()):
                print('%ifarch {}'.format(arch))
                for name in sorted(exclusives[arch]):
                    print('Provides: weakremover({})'.format(name))
                print('%endif')
