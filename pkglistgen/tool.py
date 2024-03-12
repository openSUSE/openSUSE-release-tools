import ToolBase
import glob
import logging
import os
import re
import solv
import shutil
import subprocess
import yaml

from datetime import datetime, timezone

from typing import Any, Mapping, Optional

from lxml import etree as ET

from osc.core import checkout_package

from osc.core import http_GET
from osc.core import show_results_meta
from osc.core import Package
from osc.core import undelete_package
from osclib.core import attribute_value_load
from osclib.core import target_archs
from osclib.conf import str2bool
from osclib.core import repository_path_expand
from osclib.core import repository_arch_state
from osclib.cache_manager import CacheManager
from osclib.pkglistgen_comments import PkglistComments
from osclib.repomirror import RepoMirror

from urllib.parse import urlparse

from pkglistgen import file_utils
from pkglistgen.group import Group

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))

PRODUCT_SERVICE = '/usr/lib/obs/service/create_single_product'

# share header cache with repochecker
CACHEDIR = CacheManager.directory('repository-meta')


class MismatchedRepoException(Exception):
    """raised on repos that restarted building"""


class PkgListGen(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.logger = logging.getLogger(__name__)
        self.comment = PkglistComments(self.apiurl)
        self.reset()

    def reset(self):
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
        self.filtered_architectures = None
        self.dry_run = False
        self.all_architectures = None

    def filter_architectures(self, architectures):
        self.filtered_architectures = sorted(list(set(architectures) & set(self.all_architectures)))

    def _load_supportstatus(self):
        # XXX
        fn = os.path.join(self.input_dir, 'supportstatus.txt')
        self._supportstatus = dict()
        if os.path.exists(fn):
            with open(fn, 'r') as fh:
                for line in fh:
                    # pkg, status
                    fields = line.rstrip().split(' ')
                    if len(fields) > 1:
                        self._supportstatus[fields[0]] = fields[1]

    def supportstatus(self, package):
        if self._supportstatus is None:
            self._load_supportstatus()

        return self._supportstatus.get(package)

    def _load_group_file(self, fn):
        output = None
        unwanted = None
        with open(fn, 'r') as fh:
            self.logger.debug('reading %s', fn)
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

    def group_input_files(self):
        return glob.glob(os.path.join(self.input_dir, 'group*.yml'))

    def load_all_groups(self):
        for fn in self.group_input_files():
            o, u = self._load_group_file(fn)
            if o:
                if self.output is not None:
                    raise Exception('OUTPUT defined multiple times')
                self.output = o
            if u:
                self.unwanted |= u

    # required to generate release spec files (only)
    def write_group_stubs(self):
        archs = ['*'] + self.all_architectures
        for name in self.groups:
            group = self.groups[name]
            group.solved_packages = dict()
            fn = '{}.group'.format(group.name)
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                for arch in archs:
                    x = group.toxml(arch, group.ignore_broken, None)
                    x = ET.tostring(x, pretty_print=True, encoding='unicode')
                    fh.write(x)

    def write_all_groups(self):
        self._check_supplements()
        summary = dict()
        archs = ['*'] + self.all_architectures
        for name in self.groups:
            group = self.groups[name]
            if not group.solved:
                continue
            summary[name] = group.summary()
            fn = '{}.group'.format(group.name)
            with open(os.path.join(self.output_dir, fn), 'w') as fh:
                comment = group.comment
                for arch in archs:
                    x = group.toxml(arch, group.ignore_broken, comment)
                    # only comment first time
                    comment = None
                    x = ET.tostring(x, pretty_print=True, encoding='unicode')
                    x = re.sub(r'\s*<!-- reason:', ' <!-- reason:', x)
                    fh.write(x)
        return summary

    def solve_module(self, groupname, includes, excludes, use_recommends):
        g = self.groups[groupname]
        importants = set()
        for i in includes:
            name = i
            if isinstance(i, dict):
                name = list(i)[0]
                if i[name] != 'support':
                    importants.add(name)
            else:
                importants.add(name)
            g.inherit(self.groups[name])
        g.solve(use_recommends)
        for e in excludes:
            g.ignore(self.groups[e])
        for i in importants:
            group = self.groups[i]
            for arch in group.packages:
                if arch not in g.solved_packages:
                    continue
                for package in group.packages[arch]:
                    if package[0] in g.solved_packages[arch]:
                        continue
                    if package[0] not in g.solved_packages['*']:
                        self.logger.error(f'Missing {package[0]} in {groupname} for {arch}')

    def expand_repos(self, project: str, repo='standard'):
        return repository_path_expand(self.apiurl, project, repo)

    def _check_supplements(self):
        tocheck = set()
        tocheck_locales = set()
        for arch in self.filtered_architectures:
            pool = self.prepare_pool(arch, True)
            sel = pool.Selection()
            for s in pool.solvables_iter():
                sel.add_raw(solv.Job.SOLVER_SOLVABLE, s.id)

            for s in sel.solvables():
                for dep in s.lookup_deparray(solv.SOLVABLE_SUPPLEMENTS):
                    for d in dep.str().split(' '):
                        if d.startswith('namespace:modalias') or d.startswith('namespace:filesystem'):
                            tocheck.add(s.name)

            for locale in self.locales:
                id = pool.str2id('locale({})'.format(locale))
                for s in pool.whatprovides(id):
                    tocheck_locales.add(s.name)

        all_grouped = set()
        for g in self.groups.values():
            if g.solved:
                for arch in g.solved_packages.keys():
                    if g.solved_packages[arch]:
                        all_grouped.update(g.solved_packages[arch])

        for p in tocheck - all_grouped:
            self.logger.warning('package %s has supplements but is not grouped', p)

        for p in tocheck_locales - all_grouped:
            self.logger.warning('package %s provides supported locale but is not grouped', p)

    def prepare_pool(self, arch, ignore_conflicts):
        pool = solv.Pool()
        # the i586 DVD is really a i686 one
        if arch == 'i586':
            pool.setarch('i686')
        else:
            pool.setarch(arch)

        self.lockjobs[arch] = []
        solvables = set()

        for project, reponame in self.repos:
            repo = pool.add_repo(project)
            # check back the repo state to avoid suprises
            state = repository_arch_state(self.apiurl, project, reponame, arch)
            if state is None:
                continue
            s = f'repo-{project}-{reponame}-{arch}-{state}.solv'
            if not repo.add_solv(s):
                raise MismatchedRepoException('failed to add repo {}/{}/{}'.format(project, reponame, arch))
            for solvable in repo.solvables_iter():
                if ignore_conflicts:
                    solvable.unset(solv.SOLVABLE_CONFLICTS)
                    solvable.unset(solv.SOLVABLE_OBSOLETES)
                # only take the first solvable in the repo chain
                if not self.use_newest_version and solvable.name in solvables:
                    self.lockjobs[arch].append(pool.Job(solv.Job.SOLVER_SOLVABLE | solv.Job.SOLVER_LOCK, solvable.id))
                solvables.add(solvable.name)

        pool.addfileprovides()
        pool.createwhatprovides()

        for locale in self.locales:
            pool.set_namespaceproviders(solv.NAMESPACE_LANGUAGE, pool.Dep(locale), True)

        return pool

    # parse file and merge all groups
    def _parse_unneeded(self, filename):
        filename = os.path.join(self.input_dir, filename)
        if not os.path.isfile(filename):
            return set()
        fh = open(filename, 'r')
        self.logger.debug('reading %s', filename)
        result = set()
        for group in yaml.safe_load(fh).values():
            result.update(group)
        return result

    # the unsorted group is special and will contain all the rest for
    # the FTP tree. We filter it with unneeded though to create a
    # unsorted.yml file for release manager review
    def _collect_unsorted_packages(self, modules, unsorted):
        unneeded_regexps = [re.compile(r'\A' + r + r'\Z')
                            for r in self._parse_unneeded('unneeded.yml')]

        packages = dict()
        if unsorted:
            unsorted.solved_packages = dict()
            unsorted.solved_packages['*'] = dict()

        for arch in self.filtered_architectures:
            pool = self.prepare_pool(arch, False)
            pool.Selection()
            archpacks = [s.name for s in pool.solvables_iter()]

            # copy
            filtered = list(archpacks)
            for r in unneeded_regexps:
                filtered = [p for p in filtered if not r.match(p)]

            # convert to set
            filtered = set(filtered) - self.unwanted
            for g in modules:
                if unsorted and g == unsorted:
                    continue
                for a in ('*', arch):
                    filtered -= set(g.solved_packages[a])
            for package in filtered:
                packages.setdefault(package, []).append(arch)

            if unsorted:
                archpacks = set(archpacks)
                unsorted.solved_packages[arch] = dict()
                for g in modules:
                    archpacks -= set(g.solved_packages[arch])
                    archpacks -= set(g.solved_packages['*'])
                unsorted.solved_packages[arch] = dict()
                for p in archpacks:
                    unsorted.solved_packages[arch][p] = None

        if unsorted:
            common = None
            for arch in self.filtered_architectures:
                if common is None:
                    common = set(unsorted.solved_packages[arch])
                    continue
                common &= set(unsorted.solved_packages[arch])
            for p in common:
                unsorted.solved_packages['*'][p] = None
                for arch in self.filtered_architectures:
                    del unsorted.solved_packages[arch][p]

        with open(os.path.join(self.output_dir, 'unsorted.yml'), 'w') as fh:
            fh.write('unsorted:\n')
            for p in sorted(packages):
                fh.write('  - ')
                fh.write(p)
                if len(packages[p]) != len(self.filtered_architectures):
                    fh.write(': [')
                    fh.write(','.join(sorted(packages[p])))
                    fh.write(']')
                    reason = self._find_reason(p, modules)
                    if reason:
                        fh.write(' # ' + reason)
                fh.write(' \n')

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

    def update_one_repo(self, project, repo, arch, solv_file, solv_file_hash):
        # Either hash changed or new, so remove any old hash files.
        file_utils.unlink_list(None, glob.glob(solv_file + '::*'))

        d = os.path.join(CACHEDIR, project, repo, arch)
        if not os.path.exists(d):
            os.makedirs(d)

        self.logger.debug('updating %s', d)

        rm = RepoMirror(self.apiurl)
        rm.mirror(d, project, repo, arch)

        files = [os.path.join(d, f)
                 for f in os.listdir(d) if f.endswith('.rpm')]
        suffix = f'.{os.getpid()}.tmp'
        fh = open(solv_file + suffix, 'w')
        p = subprocess.Popen(
            ['rpms2solv', '-m', '-', '-0'], stdin=subprocess.PIPE, stdout=fh)
        p.communicate(bytes('\0'.join(files), 'utf-8'))
        fh.close()
        if p.wait() != 0:
            raise Exception("rpm2solv failed")
        os.rename(solv_file + suffix, solv_file)

        # Create hash file now that solv creation is complete.
        open(solv_file_hash, 'a').close()

    def update_repos(self, architectures):
        for project, repo in self.repos:
            for arch in architectures:
                # Fetch state before mirroring in-case it changes during download.
                state = repository_arch_state(self.apiurl, project, repo, arch)
                if state is None:
                    # Repo might not have this architecture
                    continue

                repo_solv_name = 'repo-{}-{}-{}.solv'.format(project, repo, arch)
                # Would be preferable to include hash in name, but cumbersome to handle without
                # reworking a fair bit since the state needs to be tracked.
                solv_file = os.path.join(CACHEDIR, repo_solv_name)
                solv_file_hash = '{}::{}'.format(solv_file, state)
                if os.path.exists(solv_file) and os.path.exists(solv_file_hash):
                    # Solve file exists and hash unchanged, skip updating solv.
                    self.logger.debug('skipping solv generation for {} due to matching state {}'.format(
                        '/'.join([project, repo, arch]), state))
                else:
                    self.update_one_repo(project, repo, arch, solv_file, solv_file_hash)
                shutil.copy(solv_file, f'./repo-{project}-{repo}-{arch}-{state}.solv')

    def create_weakremovers(self, target, target_config, directory, output):
        drops = dict()
        dropped_repos = dict()

        root = yaml.safe_load(open(os.path.join(directory, 'config.yml')))
        for item in root:
            key = list(item)[0]
            # cast 15.1 to string :)
            key = str(key)

            oldrepos = set()
            for suffix in ['xz', 'zst']:
                oldrepos |= set(glob.glob(os.path.join(directory, f"{key}_*.packages.{suffix}")))
                oldrepos |= set(glob.glob(os.path.join(directory, f"{key}.packages.{suffix}")))
            for oldrepo in sorted(oldrepos):
                pool = solv.Pool()
                pool.setarch()

                # we need some progress in the debug output - or gocd gets nervous
                self.logger.debug('checking {}'.format(oldrepo))
                oldsysrepo = file_utils.add_susetags(pool, oldrepo)

                for arch in self.all_architectures:
                    for project, repo in self.repos:
                        # check back the repo state to avoid suprises
                        state = repository_arch_state(self.apiurl, project, repo, arch)
                        if state is None:
                            self.logger.debug(f'Skipping {project}/{repo}/{arch}')
                        fn = f'repo-{project}-{repo}-{arch}-{state}.solv'
                        r = pool.add_repo('/'.join([project, repo]))
                        if not r.add_solv(fn):
                            raise MismatchedRepoException('failed to add repo {}/{}/{}.'.format(project, repo, arch))

                pool.createwhatprovides()

                accepted_archs = set(self.all_architectures)
                accepted_archs.add('noarch')

                for s in oldsysrepo.solvables_iter():
                    oldarch = s.arch
                    if oldarch == 'i686':
                        oldarch = 'i586'

                    if oldarch not in accepted_archs:
                        continue

                    haveit = False
                    for s2 in pool.whatprovides(s.nameid):
                        if s2.repo == oldsysrepo or s.nameid != s2.nameid:
                            continue
                        newarch = s2.arch
                        if newarch == 'i686':
                            newarch = 'i586'
                        if oldarch != newarch and newarch != 'noarch' and oldarch != 'noarch':
                            continue
                        haveit = True
                        break
                    if haveit:
                        continue

                    # check for already obsoleted packages
                    nevr = pool.rel2id(s.nameid, s.evrid, solv.REL_EQ)
                    for s2 in pool.whatmatchesdep(solv.SOLVABLE_OBSOLETES, nevr):
                        if s2.repo == oldsysrepo:
                            continue
                        haveit = True
                        break
                    if haveit:
                        continue
                    if s.name not in drops:
                        drops[s.name] = {'repo': key, 'archs': set()}
                    if oldarch == 'noarch':
                        drops[s.name]['archs'] |= set(self.all_architectures)
                    else:
                        drops[s.name]['archs'].add(oldarch)
                    dropped_repos[key] = 1

                del pool

        for repo in sorted(dropped_repos):
            repo_output = False
            exclusives = dict()
            for name in sorted(drops):
                if drops[name]['repo'] != repo:
                    continue
                if drops[name]['archs'] == set(self.all_architectures):
                    if not repo_output:
                        print('#', repo, file=output)
                        repo_output = True
                    print('Provides: weakremover({})'.format(name), file=output)
                else:
                    jarch = ' '.join(sorted(drops[name]['archs']))
                    exclusives.setdefault(jarch, []).append(name)

            for arch in sorted(exclusives):
                if not repo_output:
                    print('#', repo, file=output)
                    repo_output = True
                print('%ifarch {}'.format(arch), file=output)
                for name in sorted(exclusives[arch]):
                    print('Provides: weakremover({})'.format(name), file=output)
                print('%endif', file=output)
        output.flush()

    def solve_project(
        self,
        ignore_unresolvable=False,
        ignore_recommended=False,
        locale: Optional[str] = None,
        locales_from: Optional[str] = None
    ):
        self.load_all_groups()
        if not self.output:
            self.logger.error('OUTPUT not defined')
            return

        if ignore_unresolvable:
            self.ignore_broken = True
        global_use_recommends = not ignore_recommended
        if locale:
            self.locales |= set(locale.split(' '))
        if locales_from:
            with open(os.path.join(self.input_dir, locales_from), 'r') as fh:
                root = ET.parse(fh).getroot()
                self.locales |= set([lang.text for lang in root.findall('.//linguas/language')])

        modules = []
        # the yml parser makes an array out of everything, so
        # we loop a bit more than what we support
        for group in self.output:
            groupname = list(group)[0]
            settings = group[groupname]
            if not settings:  # e.g. unsorted
                settings = {}
            includes = settings.get('includes', [])
            excludes = settings.get('excludes', [])
            use_recommends = settings.get('recommends', global_use_recommends)
            self.solve_module(groupname, includes, excludes, use_recommends)
            g = self.groups[groupname]
            # the default is a little double negated but Factory has ignore_broken
            # as default and we only disable it for single groups (for now)
            g.ignore_broken = not settings.get('require_all', not self.ignore_broken)
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

    def strip_medium_from_staging(self, path):
        # staging projects don't need source and debug medium - and the glibc source
        # rpm conflicts between standard and bootstrap_copy repository causing the
        # product builder to fail
        medium = re.compile('name="(DEBUG|SOURCE)MEDIUM"')
        for name in glob.glob(os.path.join(path, '*.kiwi')):
            lines = open(name).readlines()
            lines = [x for x in lines if not medium.search(x)]
            open(name, 'w').writelines(lines)

    def build_stub(self, destination, extension):
        with open(os.path.join(destination, '.'.join(['stub', extension])), 'w+') as f:
            f.write('# prevent building single {} files twice\n'.format(extension))
            f.write('Name: stub\n')
            f.write('Version: 0.0\n')

    def commit_package(self, path):
        if self.dry_run:
            package = Package(path)
            for i in package.get_diff():
                logging.info(''.join(i))
        else:
            # No proper API function to perform the same operation.
            logging.debug(subprocess.check_output(
                ' '.join(['cd', path, '&&', 'osc', 'addremove']), shell=True, encoding='utf-8'))
            package = Package(path)
            package.commit(msg='Automatic update', skip_local_service_run=True)

    def replace_product_version(self, product_file, product_version):
        product_version = '<version>{}</version>'.format(product_version)
        lines = open(product_file).readlines()
        new_lines = []
        for line in lines:
            new_lines.append(line.replace('<version></version>', product_version))
        open(product_file, 'w').write(''.join(new_lines))

    def update_and_solve_target(
        self,
        api,
        target_project: str,
        target_config: Mapping[str, Any],
        main_repo: str,
        project: str,
        scope: str,
        force: bool,
        no_checkout: bool,
        only_release_packages: bool,
        stop_after_solve: bool,
        custom_cache_tag
    ):
        self.all_architectures = target_config.get('pkglistgen-archs').split(' ')
        self.use_newest_version = str2bool(target_config.get('pkglistgen-use-newest-version', 'False'))
        self.repos = self.expand_repos(project, main_repo)
        logging.debug('[{}] {}/{}: update and solve'.format(scope, project, main_repo))

        group = target_config.get('pkglistgen-group', '000package-groups')
        product = target_config.get('pkglistgen-product', '000product')
        release = target_config.get('pkglistgen-release', '000release-packages')
        oldrepos = target_config.get('pkglistgen-repos', '000update-repos')

        url = api.makeurl(['source', project])
        packages = ET.parse(http_GET(url)).getroot()
        if packages.find('entry[@name="{}"]'.format(product)) is None:
            if not self.dry_run:
                undelete_package(api.apiurl, project, product, 'revive')
            # TODO disable build.
            logging.info('{} undeleted, skip dvd until next cycle'.format(product))
            return
        elif not force:
            root = ET.fromstringlist(show_results_meta(api.apiurl, project, product,
                                                       repository=[main_repo], multibuild=True))
            if len(root.xpath('result[@state="building"]')) or len(root.xpath('result[@state="dirty"]')):
                logging.info('{}/{} build in progress'.format(project, product))
                return

        drop_list = api.item_exists(project, oldrepos)
        checkout_list = [group, product, release]
        if drop_list and not only_release_packages:
            checkout_list.append(oldrepos)

        if packages.find('entry[@name="{}"]'.format(release)) is None:
            if not self.dry_run:
                undelete_package(api.apiurl, project, release, 'revive')
            logging.info('{} undeleted, skip dvd until next cycle'.format(release))
            return

        # Cache dir specific to hostname and project.
        host = urlparse(api.apiurl).hostname
        prefix_dir = 'pkglistgen'
        if custom_cache_tag:
            prefix_dir += '-' + custom_cache_tag
        cache_dir = CacheManager.directory(prefix_dir, host, project)

        if not no_checkout:
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)

        group_dir = os.path.join(cache_dir, group)
        product_dir = os.path.join(cache_dir, product)
        release_dir = os.path.join(cache_dir, release)
        oldrepos_dir = os.path.join(cache_dir, oldrepos)

        self.input_dir = group_dir
        self.output_dir = product_dir

        for package in checkout_list:
            if no_checkout:
                logging.debug('Skipping checkout of {}/{}'.format(project, package))
                continue
            checkout_package(api.apiurl, project, package, expand_link=True,
                             prj_dir=cache_dir, outdir=os.path.join(cache_dir, package))

        file_utils.unlink_all_except(release_dir, ['weakremovers.inc', '*.changes'])
        if not only_release_packages:
            file_utils.unlink_all_except(product_dir)
        ignore_list = ['supportstatus.txt', 'summary-staging.txt', 'package-groups.changes']
        ignore_list += self.group_input_files()
        file_utils.copy_directory_contents(group_dir, product_dir, ignore_list)
        file_utils.change_extension(product_dir, '.spec.in', '.spec')
        file_utils.change_extension(product_dir, '.product.in', '.product')

        logging.debug('-> do_update')
        # make sure we only calculcate existant architectures
        self.filter_architectures(target_archs(api.apiurl, project, main_repo))
        self.update_repos(self.filtered_architectures)

        if only_release_packages:
            self.load_all_groups()
            self.write_group_stubs()
        else:
            summary = self.solve_project(
                ignore_unresolvable=str2bool(target_config.get('pkglistgen-ignore-unresolvable')),
                ignore_recommended=str2bool(target_config.get('pkglistgen-ignore-recommended')),
                locale=target_config.get('pkglistgen-locale'),
                locales_from=target_config.get('pkglistgen-locales-from')
            )

        if stop_after_solve:
            return

        if drop_list and not only_release_packages:
            weakremovers_file = os.path.join(release_dir, 'weakremovers.inc')
            try:
                self.create_weakremovers(project, target_config, oldrepos_dir, output=open(weakremovers_file, 'w'))
            except MismatchedRepoException:
                logging.error("Failed to create weakremovers.inc due to mismatch in repos - project most likey started building again.")
                return

        delete_products = target_config.get('pkglistgen-delete-products', '').split(' ')
        file_utils.unlink_list(product_dir, delete_products)

        logging.debug('-> product service')
        product_version = attribute_value_load(api.apiurl, project, 'ProductVersion')
        if not product_version:
            # for stagings the product version doesn't matter (I hope)
            product_version = '1'
        for product_file in glob.glob(os.path.join(product_dir, '*.product')):
            self.replace_product_version(product_file, product_version)
            logging.debug(subprocess.check_output(
                [PRODUCT_SERVICE, product_file, product_dir, project], encoding='utf-8'))

        for delete_kiwi in target_config.get('pkglistgen-delete-kiwis-{}'.format(scope), '').split(' '):
            delete_kiwis = glob.glob(os.path.join(product_dir, delete_kiwi))
            file_utils.unlink_list(product_dir, delete_kiwis)
        if scope == 'staging':
            self.strip_medium_from_staging(product_dir)

        spec_files = glob.glob(os.path.join(product_dir, '*.spec'))
        file_utils.move_list(spec_files, release_dir)
        inc_files = glob.glob(os.path.join(group_dir, '*.inc'))
        # filter special inc file
        inc_files = filter(lambda file: file.endswith('weakremovers.inc'), inc_files)
        file_utils.move_list(inc_files, release_dir)

        # do not overwrite weakremovers.inc if it exists
        # we will commit there afterwards if needed
        if os.path.exists(os.path.join(group_dir, 'weakremovers.inc')) and \
           not os.path.exists(os.path.join(release_dir, 'weakremovers.inc')):
            file_utils.move_list([os.path.join(group_dir, 'weakremovers.inc')], release_dir)

        file_utils.multibuild_from_glob(release_dir, '*.spec')
        self.build_stub(release_dir, 'spec')

        todo_spec_files = []
        package = Package(release_dir)
        if package.get_status(False, ' '):
            todo_spec_files = glob.glob(os.path.join(release_dir, '*.spec'))
        for spec_file in todo_spec_files:
            changes_file = os.path.splitext(spec_file)[0] + '.changes'
            with open(changes_file, 'w', encoding="utf-8") as f:
                date = datetime.now(timezone.utc)
                date = date.strftime("%a %b %d %H:%M:%S %Z %Y")
                f.write(
                    "-------------------------------------------------------------------\n"
                    + date + " - openSUSE <packaging@lists.opensuse.org>\n\n"
                    "- automatically generated by openSUSE-release-tools/pkglistgen\n\n"
                )

        self.commit_package(release_dir)

        if only_release_packages:
            return

        file_utils.multibuild_from_glob(product_dir, '*.kiwi')
        self.build_stub(product_dir, 'kiwi')

        reference_summary = os.path.join(group_dir, f'summary-{scope}.txt')
        if os.path.isfile(reference_summary):
            summary_file = os.path.join(product_dir, f'summary-{scope}.txt')
            output = []
            for group in summary:
                for package in sorted(summary[group]):
                    output.append(f'{package}:{group}')

            with open(summary_file, 'w') as f:
                for line in sorted(output):
                    f.write(line + '\n')

        self.commit_package(product_dir)

        if os.path.isfile(reference_summary):
            return self.comment.handle_package_diff(project, reference_summary, summary_file)
