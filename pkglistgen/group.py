from __future__ import print_function

import logging
import re
import time

from lxml import etree as ET

import solv

ARCHITECTURES = ['x86_64', 'ppc64le', 's390x', 'aarch64']

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
        self.default_support_status = None
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
        # special feature for Tumbleweed. Just like the above but for
        # suggested (recommends are default)
        self.expand_suggested = set()

        pkglist.groups[self.safe_name] = self
        self.logger = logging.getLogger(__name__)

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
                elif rel == 'suggested':
                    self.expand_suggested.add(name)
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
        self.expand_suggested.update(group.expand_suggested)

    # do not repeat packages
    def ignore(self, without):
        for arch in ['*'] + self.pkglist.filtered_architectures:
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

    def solve(self, use_recommends=False):
        """ base: list of base groups or None """

        solved = dict()
        for arch in self.pkglist.filtered_architectures:
            solved[arch] = dict()

        self.srcpkgs = dict()
        self.recommends = dict()
        for arch in self.pkglist.filtered_architectures:
            pool = self.pkglist._prepare_pool(arch)
            solver = pool.Solver()
            solver.set_flag(solver.SOLVER_FLAG_IGNORE_RECOMMENDED, not use_recommends)

            # pool.set_debuglevel(10)
            suggested = dict()

            # packages resulting from explicit recommended expansion
            extra = []

            def solve_one_package(n, group):
                jobs = list(self.pkglist.lockjobs[arch])
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                if sel.isempty():
                    self.logger.debug('{}.{}: package {} not found'.format(self.name, arch, n))
                    self.not_found.setdefault(n, set()).add(arch)
                    return
                else:
                    if n in self.expand_recommended:
                        for s in sel.solvables():
                            for dep in s.lookup_deparray(solv.SOLVABLE_RECOMMENDS):
                                # only add recommends that exist as packages
                                rec = pool.select(dep.str(), solv.Selection.SELECTION_NAME)
                                if not rec.isempty():
                                    extra.append([dep.str(), group + ':recommended:' + n])

                    jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

                locked = self.locked | self.pkglist.unwanted
                for l in locked:
                    sel = pool.select(str(l), solv.Selection.SELECTION_NAME)
                    # if we can't find it, it probably is not as important
                    if not sel.isempty():
                        jobs += sel.jobs(solv.Job.SOLVER_LOCK)

                for s in self.silents:
                    sel = pool.select(str(s), solv.Selection.SELECTION_NAME | solv.Selection.SELECTION_FLAT)
                    if sel.isempty():
                        self.logger.warn('{}.{}: silent package {} not found'.format(self.name, arch, s))
                    else:
                        jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

                problems = solver.solve(jobs)
                if problems:
                    for problem in problems:
                        msg = 'unresolvable: {}:{}.{}: {}'.format(self.name, n, arch, problem)
                        if self.pkglist.ignore_broken:
                            self.logger.debug(msg)
                        else:
                            self.logger.debug(msg)
                        self.unresolvable[arch][n] = str(problem)
                    return

                for s in solver.get_recommended():
                    if s.name in locked:
                        continue
                    self.recommends.setdefault(s.name, group + ':' + n)
                if n in self.expand_suggested:
                    for s in solver.get_suggested():
                        suggested[s.name] = group + ':suggested:' + n

                trans = solver.transaction()
                if trans.isempty():
                    self.logger.error('%s.%s: nothing to do', self.name, arch)
                    return

                for s in trans.newsolvables():
                    solved[arch].setdefault(s.name, group + ':' + n)
                    if None:
                        reason, rule = solver.describe_decision(s)
                        print(self.name, s.name, reason, rule.info().problemstr())
                    # don't ask me why, but that's how it seems to work
                    if s.lookup_void(solv.SOLVABLE_SOURCENAME):
                        src = s.name
                    else:
                        src = s.lookup_str(solv.SOLVABLE_SOURCENAME)
                    self.srcpkgs[src] = group + ':' + s.name

            start = time.time()
            for n, group in self.packages[arch]:
                solve_one_package(n, group)

            jobs = list(self.pkglist.lockjobs[arch])
            locked = self.locked | self.pkglist.unwanted
            for l in locked:
                sel = pool.select(str(l), solv.Selection.SELECTION_NAME)
                # if we can't find it, it probably is not as important
                if not sel.isempty():
                    jobs += sel.jobs(solv.Job.SOLVER_LOCK)

            for n in solved[arch].keys() + suggested.keys():
                sel = pool.select(str(n), solv.Selection.SELECTION_NAME)
                jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

            solver.solve(jobs)
            trans = solver.transaction()
            for s in trans.newsolvables():
                solved[arch].setdefault(s.name, group + ':expansion')

            end = time.time()
            self.logger.info('%s - solving took %f', self.name, end - start)

        common = None
        # compute common packages across all architectures
        for arch in self.pkglist.filtered_architectures:
            if common is None:
                common = set(solved[arch].keys())
                continue
            common &= set(solved[arch].keys())

        if common is None:
            common = set()

        # reduce arch specific set by common ones
        solved['*'] = dict()
        for arch in self.pkglist.filtered_architectures:
            for p in common:
                solved['*'][p] = solved[arch].pop(p)

        self.solved_packages = solved
        self.solved = True

    def check_dups(self, modules, overlap):
        if not overlap:
            return
        packages = set(self.solved_packages['*'])
        for arch in self.pkglist.filtered_architectures:
            packages.update(self.solved_packages[arch])
        for m in modules:
            # do not check with ourselves and only once for the rest
            if m.name <= self.name:
                continue
            if self.name in m.conflicts or m.name in self.conflicts:
                continue
            mp = set(m.solved_packages['*'])
            for arch in self.pkglist.filtered_architectures:
                mp.update(m.solved_packages[arch])
            if len(packages & mp):
                overlap.comment += '\n overlapping between ' + self.name + ' and ' + m.name + '\n'
                for p in sorted(packages & mp):
                    for arch in m.solved_packages.keys():
                        if m.solved_packages[arch].get(p, None):
                            overlap.comment += '  # ' + m.name + '.' + arch + ': ' + m.solved_packages[arch][p] + '\n'
                        if self.solved_packages[arch].get(p, None):
                            overlap.comment += '  # ' + self.name + '.' + \
                                arch + ': ' + self.solved_packages[arch][p] + '\n'
                    overlap.comment += '  - ' + p + '\n'
                    overlap._add_to_packages(p)

    def collect_devel_packages(self):
        for arch in self.pkglist.filtered_architectures:
            pool = self.pkglist._prepare_pool(arch)
            pool.Selection()
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
                for arch in ['*'] + self.pkglist.filtered_architectures:
                    already_present = already_present or (p in m.solved_packages[arch])
            if already_present:
                del pkgdict[p]

    def filter_already_selected(self, modules):
        self._filter_already_selected(modules, self.recommends)

    def toxml(self, arch, ignore_broken=False, comment=None):
        packages = self.solved_packages.get(arch, dict())

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
                    self.logger.error(msg)
                    name = msg
            status = self.pkglist.supportstatus(name) or self.default_support_status
            attrs = {'name': name}
            if status is not None:
                attrs['supportstatus'] = status
            ET.SubElement(packagelist, 'package', attrs)
            if name in packages and packages[name]:
                c = ET.Comment(' reason: {} '.format(packages[name]))
                packagelist.append(c)

        return root

    # just list all packages in it as an array - to be output as one yml
    def summary(self):
        ret = set()
        for arch in ['*'] + self.pkglist.filtered_architectures:
            ret |= set(self.solved_packages[arch].keys())
        return ret
