from __future__ import print_function

import ToolBase
import glob
import logging
import os
import re
import solv
import shutil
import subprocess
import yaml
from lxml import etree as ET

from osc import conf
from osc.core import checkout_package

from osc.core import http_GET, http_PUT
from osc.core import HTTPError
from osc.core import show_results_meta
from osc.core import Package
from osclib.core import target_archs
from osclib.conf import Config, str2bool
from osclib.core import repository_path_expand
from osclib.core import repository_arch_state
from osclib.cache_manager import CacheManager

try:
    from urllib.parse import urljoin, urlparse
except ImportError:
    # python 2.x
    from urlparse import urljoin, urlparse

from pkglistgen import file_utils
from pkglistgen.group import Group, ARCHITECTURES

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
# share header cache with repochecker
CACHEDIR = CacheManager.directory('repository-meta')

PRODUCT_SERVICE = '/usr/lib/obs/service/create_single_product'

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
        self.filtered_architectures = None
        self.dry_run = False
        self.architectures = ARCHITECTURES

    def filter_architectures(self, architectures):
        self.filtered_architectures = list(set(architectures) & set(self.architectures))

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

    # required to generate release spec files (only)
    def write_group_stubs(self):
        archs = ['*'] + self.architectures
        for name in self.groups:
            group = self.groups[name]
            group.solved_packages = dict()
            fn = '{}.group'.format(group.name)
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                for arch in archs:
                    x = group.toxml(arch, self.ignore_broken, None)
                    x = ET.tostring(x, pretty_print=True)
                    fh.write(x)

    def write_all_groups(self):
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
                file_utils.unlink_list(None, glob.glob(solv_file + '::*'))
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

    def create_sle_weakremovers(self, target, oldprjs):
        self.repos = []
        for prj in list(oldprjs) + [target]:
            self.repos += self.expand_repos(prj, 'standard')

        self.update_repos(self.architectures)

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


    def create_droplist(self, output_dir):
        drops = dict()

        for arch in self.architectures:

            for old in oldsolv:

                logger.debug("%s: processing %s", arch, old)

                pool = solv.Pool()
                pool.setarch(arch)

                for project, repo in self.repos:
                    fn = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
                    r = pool.add_repo(project)
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
        if output_dir:
            name = os.path.join(output_dir, 'obsoletepackages.inc')
            ofh = open(name, 'w')

        for reponame in sorted(set(drops.values())):
            print("<!-- %s -->" % reponame, file=ofh)
            for p in sorted(drops):
                if drops[p] != reponame:
                    continue
                print("  <obsoletepackage>%s</obsoletepackage>" % p, file=ofh)

    def solve_project(self, ignore_unresolvable=False, ignore_recommended=False, locale=None, locales_from=None):
        """
        Generates solv from pre-published repository contained in local cache.
        Use dump_solv to extract solv from published repository.
        """

        self.load_all_groups()
        if not self.output:
            self.logger.error('OUTPUT not defined')
            return

        if ignore_unresolvable:
            self.ignore_broken = True
        global_use_recommends = not ignore_recommended
        if locale:
            for l in locale:
                self.locales |= set(l.split(','))
        if locales_from:
            with open(os.path.join(self.input_dir, locales_from), 'r') as fh:
                root = ET.parse(fh).getroot()
                self.locales |= set([lang.text for lang in root.findall(".//linguas/language")])

        modules = []
        # the yml parser makes an array out of everything, so
        # we loop a bit more than what we support
        for group in self.output:
            groupname = group.keys()[0]
            settings = group[groupname]
            if not settings:  # e.g. unsorted
                settings = {}
            includes = settings.get('includes', [])
            excludes = settings.get('excludes', [])
            use_recommends = settings.get('recommends', global_use_recommends)
            self.solve_module(groupname, includes, excludes, use_recommends)
            g = self.groups[groupname]
            g.conflicts = settings.get('conflicts', [])
            g.default_support_status = settings.get('default-support', 'unsupported')
            modules.append(g)

        # not defined for openSUSE
        overlap = self.groups.get('overlap')
        for module in modules:
            module.check_dups(modules, overlap)
            module.collect_devel_packages()
            module.filter_already_selected(modules)

        if overlap:
            ignores = [x.name for x in overlap.ignored]
            self.solve_module(overlap.name, [], ignores, use_recommends=False)
            overlapped = set(overlap.solved_packages['*'])
            for arch in self.filtered_architectures:
                overlapped |= set(overlap.solved_packages[arch])
            for module in modules:
                if module.name == 'overlap' or module in overlap.ignored:
                    continue
                for arch in ['*'] + self.filtered_architectures:
                    for p in overlapped:
                        module.solved_packages[arch].pop(p, None)

        self._collect_unsorted_packages(modules, self.groups.get('unsorted'))
        return self.write_all_groups()


    # staging projects don't need source and debug medium - and the glibc source
    # rpm conflicts between standard and bootstrap_copy repository causing the
    # product builder to fail
    def strip_medium_from_staging(self, path):
        medium = re.compile('name="(DEBUG|SOURCE)MEDIUM"')
        for name in glob.glob(os.path.join(path, '*.kiwi')):
            lines = open(name).readlines()
            lines = [l for l in lines if not medium.search(l)]
            open(name, 'w').writelines(lines)

    def build_stub(self, destination, extension):
        f = file(os.path.join(destination, '.'.join(['stub', extension])), 'w+')
        f.write('# prevent building single {} files twice\n'.format(extension))
        f.write('Name: stub\n')
        f.write('Version: 0.0\n')
        f.close()

    def commit_package(self, path):
        if self.dry_run:
            package = Package(path)
            for i in package.get_diff():
                print(''.join(i))
        else:
            # No proper API function to perform the same operation.
            print(subprocess.check_output(
                ' '.join(['cd', path, '&&', 'osc', 'addremove']), shell=True))
            package = Package(path)
            package.commit(msg='Automatic update', skip_local_service_run=True)

    def update_and_solve_target(self, api, target_project, target_config, main_repo,
                                project, scope, force, no_checkout,
                                only_release_packages, stop_after_solve, drop_list=False):
        self.repos = self.expand_repos(project, main_repo)
        print('[{}] {}/{}: update and solve'.format(scope, project, main_repo))

        group = target_config.get('pkglistgen-group', '000package-groups')
        product = target_config.get('pkglistgen-product', '000product')
        release = target_config.get('pkglistgen-release', '000release-packages')

        url = api.makeurl(['source', project])
        packages = ET.parse(http_GET(url)).getroot()
        if packages.find('entry[@name="{}"]'.format(product)) is None:
            if not self.dry_run:
                undelete_package(api.apiurl, project, product, 'revive')
            # TODO disable build.
            print('{} undeleted, skip dvd until next cycle'.format(product))
            return
        elif not force:
            root = ET.fromstringlist(show_results_meta(api.apiurl, project, product,
                                                       repository=[main_repo], multibuild=True))
            if len(root.xpath('result[@state="building"]')) or len(root.xpath('result[@state="dirty"]')):
                print('{}/{} build in progress'.format(project, product))
                return

        checkout_list = [group, product, release]

        if packages.find('entry[@name="{}"]'.format(release)) is None:
            if not self.dry_run:
                undelete_package(api.apiurl, project, release, 'revive')
            print('{} undeleted, skip dvd until next cycle'.format(release))
            return

        # Cache dir specific to hostname and project.
        host = urlparse(api.apiurl).hostname
        cache_dir = CacheManager.directory('pkglistgen', host, project)

        if not no_checkout:
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)

        group_dir = os.path.join(cache_dir, group)
        product_dir = os.path.join(cache_dir, product)
        release_dir = os.path.join(cache_dir, release)

        for package in checkout_list:
            if no_checkout:
                print("Skipping checkout of {}/{}".format(project, package))
                continue
            checkout_package(api.apiurl, project, package, expand_link=True, prj_dir=cache_dir)

        file_utils.unlink_all_except(release_dir)
        if not only_release_packages:
            file_utils.unlink_all_except(product_dir)
        file_utils.copy_directory_contents(group_dir, product_dir,
                                     ['supportstatus.txt', 'groups.yml',
                                      'reference-unsorted.yml', 'reference-summary.yml',
                                      'package-groups.changes'])
        file_utils.change_extension(product_dir, '.spec.in', '.spec')
        file_utils.change_extension(product_dir, '.product.in', '.product')

        self.input_dir = group_dir
        self.output_dir = product_dir

        print('-> do_update')
        # make sure we only calculcate existant architectures
        self.filter_architectures(target_archs(api.apiurl, project, main_repo))
        self.update_repos(self.filtered_architectures)

        nonfree = target_config.get('nonfree')
        if nonfree and drop_list:
            print('-> do_update nonfree')

            # Switch to nonfree repo (ugly, but that's how the code was setup).
            repos_ = self.repos
            self.repos = self.expand_repos(nonfree, main_repo)
            self.update_repos(self.filtered_architectures)

            # Switch repo back to main target project.
            self.repos = repos_

            print('-> update_merge')
            solv_utils.update_merge(nonfree if drop_list else False, self.repos, self.architectures)

        if only_release_packages:
            self.load_all_groups()
            self.write_group_stubs()
        else:
            summary = self.solve_project(ignore_unresolvable=str2bool(target_config.get('pkglistgen-ignore-unresolvable')),
                                         ignore_recommended=str2bool(target_config.get('pkglistgen-ignore-recommended')),
                                         locale = target_config.get('pkglistgen-local'),
                                         locales_from = target_config.get('pkglistgen-locales-from'))

        if stop_after_solve:
            return

        if drop_list:
            # Ensure solv files from all releases in product family are updated.
            print('-> solv_cache_update')
            cache_dir_solv = CacheManager.directory('pkglistgen', 'solv')
            family_last = target_config.get('pkglistgen-product-family-last')
            family_include = target_config.get('pkglistgen-product-family-include')
            solv_prior = solv_utils.solv_cache_update(api.apiurl, cache_dir_solv, target_project, family_last, family_include)

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
        file_utils.unlink_list(product_dir, delete_products)

        print('-> product service')
        for product_file in glob.glob(os.path.join(product_dir, '*.product')):
            print(subprocess.check_output(
                [PRODUCT_SERVICE, product_file, product_dir, project]))

        for delete_kiwi in target_config.get('pkglistgen-delete-kiwis-{}'.format(scope), '').split(' '):
            delete_kiwis = glob.glob(os.path.join(product_dir, delete_kiwi))
            file_utils.unlink_list(product_dir, delete_kiwis)
        if scope == 'staging':
            self.strip_medium_from_staging(product_dir)

        spec_files = glob.glob(os.path.join(product_dir, '*.spec'))
        file_utils.move_list(spec_files, release_dir)
        inc_files = glob.glob(os.path.join(group_dir, '*.inc'))
        file_utils.move_list(inc_files, release_dir)

        file_utils.multibuild_from_glob(release_dir, '*.spec')
        self.build_stub(release_dir, 'spec')
        self.commit_package(release_dir)

        if only_release_packages:
            return

        file_utils.multibuild_from_glob(product_dir, '*.kiwi')
        self.build_stub(product_dir, 'kiwi')
        self.commit_package(product_dir)

        reference_summary = os.path.join(group_dir, 'reference-summary.yml')
        if os.path.isfile(reference_summary):
            summary_file = os.path.join(product_dir, 'summary.yml')
            with open(summary_file, 'w') as f:
                f.write("# Summary of packages in groups")
                for group in sorted(summary.keys()):
                    # the unsorted group should appear filtered by
                    # unneeded.yml - so we need the content of unsorted.yml
                    # not unsorted.group (this grew a little unnaturally)
                    if group == 'unsorted':
                        continue
                    f.write("\n" + group + ":\n")
                    for package in sorted(summary[group]):
                        f.write("  - " + package + "\n")


            try:
                subprocess.check_output(["diff", "-u", reference_summary, summary_file])
            except subprocess.CalledProcessError, e:
                print(e.output)
                self.error_occured = True
            reference_unsorted = os.path.join(group_dir, 'reference-unsorted.yml')
            unsorted_file = os.path.join(product_dir, 'unsorted.yml')
            try:
                subprocess.check_output(["diff", "-u", reference_unsorted, unsorted_file])
            except subprocess.CalledProcessError, e:
                print(e.output)
                self.error_occured = True
