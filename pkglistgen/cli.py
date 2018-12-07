#!/usr/bin/python

# TODO: solve all devel packages to include
from __future__ import print_function

import copy
import filecmp
import glob
import logging
import os
import os.path
import re
import shutil
import string
import subprocess
import sys
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

import solv

import yaml

import ToolBase

from pkglistgen import solv_utils
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
        tool.init_architectures(self.options.architectures)
        return tool

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

    def do_create_sle_weakremovers(self, subcmd, opts, target, *prjs):
        """${cmd_name}: generate list of obsolete packages for SLE

        The globally specified repositories are taken as the current
        package set. All solv files specified on the command line
        are old versions of those repos.

        The command outputs the weakremovers.inc to be used in
        000package-groups

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.tool.create_sle_weakremovers(target, prjs)

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
        return solv_utils.dump_solv(baseurl=baseurl, output_dir=self.options.output_dir, overwrite=opts.overwrite)

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
            self.update_and_solve_target_wrapper(api, target_project, target_config, main_repo,
             project=opts.project, scope=opts.scope, force=opts.force,
              no_checkout=opts.no_checkout, only_release_packages=opts.only_release_packages,
              stop_after_solve=opts.stop_after_solve,
                                                 drop_list=True)
        elif opts.scope == 'rings':
            opts.project = api.rings[1]
            self.repos = self.tool.expand_repos(api.rings[1], main_repo)
            self.update_and_solve_target_wrapper(api, target_project, target_config, main_repo,
                                                 project=opts.project, scope=opts.scope, force=opts.force,
                                                  no_checkout=opts.no_checkout, only_release_packages=opts.only_release_packages,
                                                  stop_after_solve=opts.stop_after_solve)
        elif opts.scope == 'staging':
            letters = api.get_staging_projects_short()
            for letter in letters:
                if opts.staging_project and letter != opts.staging_project:
                    continue
                opts.project = api.prj_from_short(letter)
                self.repos = self.tool.expand_repos(opts.project, main_repo)
                self.update_and_solve_target_wrapper(api, target_project, target_config, main_repo,
                                                     project=opts.project, scope=opts.scope, force=opts.force,
                                                      no_checkout=opts.no_checkout, only_release_packages=opts.only_release_packages,
                                                      stop_after_solve=opts.stop_after_solve)
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
                print('-> dump_solv for {}/{}'.format(
                    project_display, os.path.basename(os.path.normpath(url))))
                logger.debug(url)

                output_dir = os.path.join(cache_dir_solv, project)
                if not os.path.exists(output_dir):
                    os.makedirs(output_dir)

                solv_name = solv_utils.dump_solv(baseurl=url, output_dir=output_dir, overwrite=False)
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
