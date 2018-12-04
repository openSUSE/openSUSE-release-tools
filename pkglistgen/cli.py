#!/usr/bin/python

# TODO: implement equivalent of namespace namespace:language(de) @SYSTEM
# TODO: solve all devel packages to include
from __future__ import print_function

import copy
import filecmp
import glob
import gzip
import hashlib
import io
import logging
import os
import os.path
import random
import re
import shutil
import string
import subprocess
import sys
import tempfile
import traceback

import cmdln

from lxml import etree as ET

from osc import conf
from osc.core import checkout_package
from osc.core import http_GET, http_PUT
from osc.core import HTTPError
from osc.core import makeurl
from osc.core import Package
from osc.core import show_results_meta
from osc.core import undelete_package
from osclib.cache_manager import CacheManager
from osclib.conf import Config, str2bool
from osclib.core import source_file_ensure
from osclib.core import target_archs
from osclib.stagingapi import StagingAPI
from osclib.util import project_list_family
from osclib.util import project_list_family_prior
try:
    from urllib.parse import urljoin, urlparse
except ImportError:
    # python 2.x
    from urlparse import urljoin, urlparse

import requests

import solv

import yaml

import ToolBase

from pkglistgen.group import ARCHITECTURES
from pkglistgen.tool import PkgListGen, CACHEDIR

logger = logging.getLogger()

PRODUCT_SERVICE = '/usr/lib/obs/service/create_single_product'

class CommandLineInterface(ToolBase.CommandLineInterface):
    SCOPES = ['all', 'target', 'rings', 'staging']

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)
        self.repos = []

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-i', '--input-dir', dest='input_dir', metavar='DIR',
                          help='input directory', default='.')
        parser.add_option('-o', '--output-dir', dest='output_dir', metavar='DIR',
                          help='input directory', default='.')
        parser.add_option('-a', '--architecture', dest='architectures', metavar='ARCH',
                          help='architecure', action='append')
        return parser

    def setup_tool(self):
        tool = PkgListGen()
        tool.input_dir = self.options.input_dir
        tool.output_dir = self.options.output_dir
        tool.repos = self.repos
        if self.options.architectures:
            tool.architectures = self.options.architectures
        else:
            tool.architectures = ARCHITECTURES
        return tool

    def do_list(self, subcmd, opts):
        """${cmd_name}: list all groups

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.load_all_groups()

        for name in sorted(self.tool.groups.keys()):
            print(name)

    def do_list_products(self, subcmd, opts):
        """${cmd_name}: list all products

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.list_products()

    def do_update(self, subcmd, opts):
        """${cmd_name}: Update groups

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.tool.update_repos(opts)

    def update_merge(self, nonfree):
        """Merge free and nonfree solv files or copy free to merged"""
        for project, repo in self.repos:
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
            self.tool.expand_repos(prj, 'standard')
            opts.project = prj
            self.tool.update_repos(opts)

        drops = dict()
        for arch in self.tool.architectures:
            pool = solv.Pool()
            pool.setarch(arch)

            sysrepo = None
            for prp in prjs:
                fn = os.path.join(CACHEDIR, 'repo-{}-{}-{}.solv'.format(prp, 'standard', arch))
                r = pool.add_repo('/'.join([prj, 'standard']))
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

                for project, repo in self.tool.repos:
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
        url = urljoin(baseurl, path_prefix + 'repodata/repomd.xml')
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
        url = urljoin(baseurl, path_prefix + location)
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

        url = urljoin(baseurl, 'media.1/media')
        with requests.get(url) as media:
            for i, line in enumerate(media.iter_lines()):
                if i != 1:
                    continue
                name = line

        if name is not None and '-Build' in name:
            return name, 'media'

        url = urljoin(baseurl, 'media.1/build')
        with requests.get(url) as build:
            name = build.content.strip()

        if name is not None and '-Build' in name:
            return name, 'build'

        raise Exception(baseurl + '/media.1/{media,build} includes no build number')

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
        global_use_recommends = not opts.ignore_recommended
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
        self.tool.filtered_architectures = opts.filtered_architectures

        modules = []
        # the yml parser makes an array out of everything, so
        # we loop a bit more than what we support
        for group in self.tool.output:
            groupname = group.keys()[0]
            settings = group[groupname]
            if not settings:  # e.g. unsorted
                settings = {}
            includes = settings.get('includes', [])
            excludes = settings.get('excludes', [])
            use_recommends = settings.get('recommends', global_use_recommends)
            self.tool.solve_module(groupname, includes, excludes, use_recommends)
            g = self.tool.groups[groupname]
            g.conflicts = settings.get('conflicts', [])
            g.default_support_status = settings.get('default-support', 'unsupported')
            modules.append(g)

        # not defined for openSUSE
        overlap = self.tool.groups.get('overlap')
        for module in modules:
            module.check_dups(modules, overlap)
            module.collect_devel_packages()
            module.filter_already_selected(modules)

        if overlap:
            ignores = [x.name for x in overlap.ignored]
            self.tool.solve_module(overlap.name, [], ignores, use_recommends=False)
            overlapped = set(overlap.solved_packages['*'])
            for arch in self.tool.filtered_architectures:
                overlapped |= set(overlap.solved_packages[arch])
            for module in modules:
                if module.name == 'overlap' or module in overlap.ignored:
                    continue
                for arch in ['*'] + self.tool.filtered_architectures:
                    for p in overlapped:
                        module.solved_packages[arch].pop(p, None)

        self.tool._collect_unsorted_packages(modules, self.tool.groups.get('unsorted'))
        return self.tool._write_all_groups()

    @cmdln.option('-f', '--force', action='store_true', help='continue even if build is in progress')
    @cmdln.option('-p', '--project', help='target project')
    @cmdln.option('-s', '--scope', action='append', default=['all'], help='scope on which to operate ({}, staging:$letter)'.format(', '.join(SCOPES)))
    @cmdln.option('--no-checkout', action='store_true', help='reuse checkout in cache')
    @cmdln.option('--stop-after-solve', action='store_true', help='only create group files')
    @cmdln.option('--staging', help='Only solve that one staging')
    @cmdln.option('--only-release-packages', action='store_true', help='Generate 000release-packages only')
    def do_update_and_solve(self, subcmd, opts):
        """${cmd_name}: update and solve for given scope

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.error_occured = False

        if opts.staging:
            match = re.match('(.*):Staging:(.*)', opts.staging)
            opts.scope = ['staging:' + match.group(2)]
            opts.project = match.group(1)

        if not opts.project:
            raise ValueError('project is required')
        opts.staging_project = None

        apiurl = conf.config['apiurl']
        config = Config(apiurl, opts.project)
        target_config = conf.config[opts.project]

        if apiurl.find('suse.de') > 0:
            # used by product converter
            os.environ['OBS_NAME'] = 'build.suse.de'

        # special case for all
        if opts.scope == ['all']:
            opts.scope = target_config.get('pkglistgen-scopes', 'target').split(' ')

        for scope in opts.scope:
            if scope.startswith('staging:'):
                opts.staging_project = re.match('staging:(.*)', scope).group(1)
                opts.staging_project = opts.staging_project.upper()
                scope = 'staging'
            if scope not in self.SCOPES:
                raise ValueError('scope "{}" must be one of: {}'.format(scope, ', '.join(self.SCOPES)))
            opts.scope = scope
            self.real_update_and_solve(target_config, copy.deepcopy(opts))
        return self.error_occured

    # note: scope is a value here - while it's an array above
    def real_update_and_solve(self, target_config, opts):
        # Store target project as opts.project will contain subprojects.
        target_project = opts.project

        apiurl = conf.config['apiurl']
        api = StagingAPI(apiurl, target_project)

        archs_key = 'pkglistgen-archs'

        if archs_key in target_config:
            self.options.architectures = target_config.get(archs_key).split(' ')
        main_repo = target_config['main-repo']

        if opts.scope == 'target':
            self.repos = self.tool.expand_repos(target_project, main_repo)
            self.update_and_solve_target_wrapper(api, target_project, target_config, main_repo, opts, drop_list=True)
        elif opts.scope == 'rings':
            opts.project = api.rings[1]
            self.repos = self.tool.expand_repos(api.rings[1], main_repo)
            self.update_and_solve_target_wrapper(api, target_project, target_config, main_repo, opts)
        elif opts.scope == 'staging':
            letters = api.get_staging_projects_short()
            for letter in letters:
                if opts.staging_project and letter != opts.staging_project:
                    continue
                opts.project = api.prj_from_short(letter)
                self.repos = self.tool.expand_repos(opts.project, main_repo)
                self.update_and_solve_target_wrapper(api, target_project, target_config, main_repo, opts)
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

    def update_and_solve_target(self, api, target_project, target_config, main_repo, opts,
                                drop_list=False):
        print('[{}] {}/{}: update and solve'.format(opts.scope, opts.project, main_repo))

        group = target_config.get('pkglistgen-group', '000package-groups')
        product = target_config.get('pkglistgen-product', '000product')
        release = target_config.get('pkglistgen-release', '000release-packages')

        opts.filtered_architectures = []
        # make sure we only calculcate existant architectures
        for arch in target_archs(api.apiurl, opts.project, main_repo):
            if arch in self.options.architectures:
                opts.filtered_architectures.append(arch)

        url = api.makeurl(['source', opts.project])
        packages = ET.parse(http_GET(url)).getroot()
        if packages.find('entry[@name="{}"]'.format(product)) is None:
            if not self.options.dry:
                undelete_package(api.apiurl, opts.project, product, 'revive')
            # TODO disable build.
            print('{} undeleted, skip dvd until next cycle'.format(product))
            return
        elif not opts.force:
            root = ET.fromstringlist(show_results_meta(api.apiurl, opts.project, product,
                                                       repository=[main_repo], multibuild=True))
            if len(root.xpath('result[@state="building"]')) or len(root.xpath('result[@state="dirty"]')):
                print('{}/{} build in progress'.format(opts.project, product))
                return

        checkout_list = [group, product, release]

        if packages.find('entry[@name="{}"]'.format(release)) is None:
            if not self.options.dry:
                undelete_package(api.apiurl, opts.project, release, 'revive')
            print('{} undeleted, skip dvd until next cycle'.format(release))
            return

        # Cache dir specific to hostname and project.
        host = urlparse(api.apiurl).hostname
        cache_dir = CacheManager.directory('pkglistgen', host, opts.project)

        if not opts.no_checkout:
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)

        group_dir = os.path.join(cache_dir, group)
        product_dir = os.path.join(cache_dir, product)
        release_dir = os.path.join(cache_dir, release)

        for package in checkout_list:
            if opts.no_checkout:
                print("Skipping checkout of {}/{}".format(opts.project, package))
                continue
            checkout_package(api.apiurl, opts.project, package, expand_link=True, prj_dir=cache_dir)

        self.unlink_all_except(release_dir)
        if not opts.only_release_packages:
            self.unlink_all_except(product_dir)
        self.copy_directory_contents(group_dir, product_dir,
                                     ['supportstatus.txt', 'groups.yml', 'package-groups.changes'])
        self.change_extension(product_dir, '.spec.in', '.spec')
        self.change_extension(product_dir, '.product.in', '.product')

        self.options.input_dir = group_dir
        self.options.output_dir = product_dir
        self.postoptparse()

        print('-> do_update')
        self.tool.update_repos(opts)

        nonfree = target_config.get('nonfree')
        if nonfree and drop_list:
            print('-> do_update nonfree')

            # Switch to nonfree repo (ugly, but that's how the code was setup).
            repos_ = self.repos
            opts_nonfree = copy.deepcopy(opts)
            opts_nonfree.project = nonfree
            self.tool.repos = self.tool.expand_repos(nonfree, main_repo)
            self.tool.update_repos(opts_nonfree)

            # Switch repo back to main target project.
            self.tool.repos = repos_

            print('-> update_merge')
            self.update_merge(nonfree if drop_list else False)

        print('-> do_solve')
        opts.ignore_unresolvable = str2bool(target_config.get('pkglistgen-ignore-unresolvable'))
        opts.ignore_recommended = str2bool(target_config.get('pkglistgen-ignore-recommended'))
        opts.include_suggested = str2bool(target_config.get('pkglistgen-include-suggested'))
        opts.locale = target_config.get('pkglistgen-local')
        opts.locales_from = target_config.get('pkglistgen-locales-from')
        if not opts.only_release_packages:
            summary = self.do_solve('solve', opts)

        if opts.stop_after_solve:
            return

        if drop_list:
            # Ensure solv files from all releases in product family are updated.
            print('-> solv_cache_update')
            cache_dir_solv = CacheManager.directory('pkglistgen', 'solv')
            family_last = target_config.get('pkglistgen-product-family-last')
            family_include = target_config.get('pkglistgen-product-family-include')
            solv_prior = self.solv_cache_update(
                api.apiurl, cache_dir_solv, target_project, family_last, family_include, opts)

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
        self.tool.unlink_list(product_dir, delete_products)

        print('-> product service')
        for product_file in glob.glob(os.path.join(product_dir, '*.product')):
            print(subprocess.check_output(
                [PRODUCT_SERVICE, product_file, product_dir, opts.project]))

        for delete_kiwi in target_config.get('pkglistgen-delete-kiwis-{}'.format(opts.scope), '').split(' '):
            delete_kiwis = glob.glob(os.path.join(product_dir, delete_kiwi))
            self.tool.unlink_list(product_dir, delete_kiwis)
        if opts.scope == 'staging':
            self.strip_medium_from_staging(product_dir)

        spec_files = glob.glob(os.path.join(product_dir, '*.spec'))
        self.move_list(spec_files, release_dir)
        inc_files = glob.glob(os.path.join(group_dir, '*.inc'))
        self.move_list(inc_files, release_dir)

        self.multibuild_from_glob(release_dir, '*.spec')
        self.build_stub(release_dir, 'spec')
        self.commit_package(release_dir)

        if opts.only_release_packages:
            return

        self.multibuild_from_glob(product_dir, '*.kiwi')
        self.build_stub(product_dir, 'kiwi')
        self.commit_package(product_dir)

        if api.item_exists(opts.project, '000product-summary'):
            summary_str = "# Summary of packages in groups"
            for group in sorted(summary.keys()):
                # the unsorted group should appear filtered by
                # unneeded.yml - so we need the content of unsorted.yml
                # not unsorted.group (this grew a little unnaturally)
                if group == 'unsorted':
                    continue
                summary_str += "\n" + group + ":\n"
                for package in sorted(summary[group]):
                    summary_str += "  - " + package + "\n"

            source_file_ensure(api.apiurl, opts.project, '000product-summary',
                               'summary.yml', summary_str, 'Updating summary.yml')
            unsorted_yml = open(os.path.join(product_dir, 'unsorted.yml')).read()
            source_file_ensure(api.apiurl, opts.project, '000product-summary',
                               'unsorted.yml', unsorted_yml, 'Updating unsorted.yml')

    def solv_cache_update(self, apiurl, cache_dir_solv, target_project, family_last, family_include, opts):
        """Dump solv files (do_dump_solv) for all products in family."""
        prior = set()

        project_family = project_list_family_prior(
            apiurl, target_project, include_self=True, last=family_last)
        if family_include:
            # Include projects from a different family if desired.
            project_family.extend(project_list_family(apiurl, family_include))

        for project in project_family:
            config = Config(apiurl, project)
            project_config = conf.config[project]

            baseurl = project_config.get('download-baseurl')
            if not baseurl:
                baseurl = project_config.get('download-baseurl-' + project.replace(':', '-'))
            baseurl_update = project_config.get('download-baseurl-update')
            if not baseurl:
                logger.warning('no baseurl configured for {}'.format(project))
                continue

            urls = [urljoin(baseurl, 'repo/oss/')]
            if baseurl_update:
                urls.append(urljoin(baseurl_update, 'oss/'))
            if project_config.get('nonfree'):
                urls.append(urljoin(baseurl, 'repo/non-oss/'))
                if baseurl_update:
                    urls.append(urljoin(baseurl_update, 'non-oss/'))

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
                solv_name = self.do_dump_solv('dump_solv', opts, url)
                if solv_name:
                    names.append(solv_name)

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
        if self.options.dry:
            package = Package(path)
            for i in package.get_diff():
                print(''.join(i))
        else:
            # No proper API function to perform the same operation.
            print(subprocess.check_output(
                ' '.join(['cd', path, '&&', 'osc', 'addremove']), shell=True))
            package = Package(path)
            package.commit(msg='Automatic update', skip_local_service_run=True)
