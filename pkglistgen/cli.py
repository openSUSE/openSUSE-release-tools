#!/usr/bin/python

# TODO: solve all devel packages to include
from __future__ import print_function

import cmdln
import os
import re
import ToolBase
import traceback

from osc import conf
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from pkglistgen import solv_utils
from pkglistgen.tool import PkgListGen, CACHEDIR

class CommandLineInterface(ToolBase.CommandLineInterface):
    SCOPES = ['all', 'target', 'rings', 'staging']

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def setup_tool(self):
        tool = PkgListGen()
        tool.dry_run = self.options.dry
        return tool

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

    @cmdln.option('-o', '--output-dir', dest='output_dir', metavar='DIR', help='output directory', default='.')
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
        return self.tool.create_droplist(oldsolv, output_dir=self.options.output_dir)

    @cmdln.option('-o', '--output-dir', dest='output_dir', metavar='DIR', help='output directory', default='.')
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
    @cmdln.option('-s', '--scope', action='append', help='scope on which to operate ({}, staging:$letter)'.format(', '.join(SCOPES)))
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
            if opts.project:
                raise ValueError('--staging and --project conflict')
            opts.project = match.group(1)
        elif not opts.project:
            raise ValueError('project is required')
        elif not opts.scope:
            opts.scope = ['all']

        apiurl = conf.config['apiurl']
        config = Config(apiurl, opts.project)
        target_config = conf.config[opts.project]

        # Store target project as opts.project will contain subprojects.
        target_project = opts.project

        api = StagingAPI(apiurl, target_project)

        archs_key = 'pkglistgen-archs'

        if archs_key in target_config:
            self.options.architectures = target_config.get(archs_key).split(' ')
        main_repo = target_config['main-repo']

        if apiurl.find('suse.de') > 0:
            # used by product converter
            os.environ['OBS_NAME'] = 'build.suse.de'

        print('scope', opts.scope)
        # special case for all
        if opts.scope == ['all']:
            opts.scope = target_config.get('pkglistgen-scopes', 'target').split(' ')

        def solve_project(project, scope):
            try:
                self.tool.update_and_solve_target(api, target_project, target_config, main_repo,
                                project=project, scope=scope, force=opts.force,
                                no_checkout=opts.no_checkout,
                                only_release_packages=opts.only_release_packages,
                                stop_after_solve=opts.stop_after_solve, drop_list=(scope == 'target'))
            except Exception as e:
                # Print exception, but continue to prevent problems effecting one
                # project from killing the whole process. Downside being a common
                # error will be duplicated for each project. Common exceptions could
                # be excluded if a set list is determined, but that is likely not
                # practical.
                traceback.print_exc()
                self.error_occured = True

        for scope in opts.scope:
            if scope.startswith('staging:'):
                letter = re.match('staging:(.*)', scope).group(1)
                solve_project(api.prj_from_short(letter.upper()), 'staging')
            elif scope == 'target':
                solve_project(target_project, scope)
            elif scope == 'rings':
                solve_project(api.rings[1], scope)
            elif scope == 'staging':
                letters = api.get_staging_projects_short()
                for letter in letters:
                    solve_project(api.prj_from_short(letter), scope)
            else:
                raise ValueError('scope "{}" must be one of: {}'.format(scope, ', '.join(self.SCOPES)))

        return self.error_occured
