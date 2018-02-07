# Copyright (C) 2015 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from __future__ import print_function

import os
import os.path
import subprocess
import sys
import tempfile
import warnings
import yaml

import colorama
from colorama import Fore
from colorama import ansi

from osc import cmdln
from osc import conf
from osc import core
from osc import oscerr

from osclib.accept_command import AcceptCommand
from osclib.adi_command import AdiCommand
from osclib.check_command import CheckCommand
from osclib.check_duplicate_binaries_command import CheckDuplicateBinariesCommand
from osclib.cleanup_rings import CleanupRings
from osclib.conf import Config
from osclib.config_command import ConfigCommand
from osclib.freeze_command import FreezeCommand
from osclib.ignore_command import IgnoreCommand
from osclib.unignore_command import UnignoreCommand
from osclib.list_command import ListCommand
from osclib.obslock import OBSLock
from osclib.select_command import SelectCommand
from osclib.stagingapi import StagingAPI
from osclib.cache import Cache
from osclib.unselect_command import UnselectCommand
from osclib.repair_command import RepairCommand
from osclib.rebuild_command import RebuildCommand
from osclib.request_splitter import RequestSplitter
from osclib.supersede_command import SupersedeCommand
from osclib.prio_command import PrioCommand

OSC_STAGING_VERSION = '0.0.1'


def _print_version(self):
    """ Print version information about this extension. """
    print(OSC_STAGING_VERSION)
    quit(0)


def _full_project_name(self, project):
    """Deduce the full project name."""
    if project.startswith(('openSUSE', 'SUSE')):
        return project

    if 'Factory' in project or 'openSUSE' in project:
        return 'openSUSE:%s' % project

    if 'SLE' in project:
        return 'SUSE:%s' % project

    # If we can't guess, raise a Warning
    warnings.warn('%s project not recognized.' % project)
    return project

def lock_needed(cmd, opts):
    return not(
        cmd in ('acheck', 'check', 'check_duplicate_binaries', 'frozenage', 'rebuild', 'unlock') or
        (cmd == 'list' and not opts.supersede)
    )

def clean_args(args):
    out = []
    for arg in args:
        if arg == 'and':
            continue
        if ' ' not in arg:
            arg = arg.rstrip(',')
            if ',' in arg:
                out.extend(arg.split(','))
                continue
        out.append(arg)
    return out


@cmdln.option('--move', action='store_true',
              help='force the selection to become a move')
@cmdln.option('--by-develproject', action='store_true',
              help='split the requests by devel project')
@cmdln.option('--split', action='store_true',
              help='split the requests into individual groups')
@cmdln.option('--supersede', action='store_true',
              help='replace staged requests when superseded')
@cmdln.option('-f', '--from', dest='from_', metavar='FROMPROJECT',
              help='specify a source project when moving a request')
@cmdln.option('-p', '--project', dest='project', metavar='PROJECT',
              help='indicate the project on which to operate, default is openSUSE:Factory')
@cmdln.option('--add', dest='add', metavar='PACKAGE',
              help='mark additional packages to be checked by repo checker')
@cmdln.option('--force', action='store_true',
              help='force action, overruling internal checks (CAUTION)')
@cmdln.option('-o', '--old', action='store_true',
              help='use the old check algorithm')
@cmdln.option('-v', '--version', action='store_true',
              help='print the plugin version')
@cmdln.option('--no-freeze', dest='no_freeze', action='store_true',
              help='force the select command ignoring the time from the last freeze')
@cmdln.option('--cleanup', action='store_true', help='cleanup after completing operation')
@cmdln.option('--no-cleanup', dest='no_cleanup', action='store_true',
              help='do not cleanup remaining packages in staging projects after accept')
@cmdln.option('--no-bootstrap', dest='bootstrap', action='store_false', default=True,
              help='do not update bootstrap-copy when freezing')
@cmdln.option('--wipe-cache', dest='wipe_cache', action='store_true', default=False,
              help='wipe GET request cache before executing')
@cmdln.option('-m', '--message', help='message used by ignore command')
@cmdln.option('--filter-by', action='append', help='xpath by which to filter requests')
@cmdln.option('--group-by', action='append', help='xpath by which to group requests')
@cmdln.option('-i', '--interactive', action='store_true', help='interactively modify selection proposal')
@cmdln.option('-n', '--non-interactive', action='store_true', help='do not ask anything, use default answers')
@cmdln.option('--merge', action='store_true', help='propose merge where applicable and store details to allow future merges')
@cmdln.option('--try-strategies', action='store_true', default=False, help='apply strategies and keep any with desireable outcome')
@cmdln.option('--strategy', help='apply a specific strategy')
@cmdln.option('--no-color', action='store_true', help='strip colors from output (or add staging.color = 0 to the .oscrc general section')
@cmdln.option('--save', action='store_true', help='save the result to the dashboard container')
@cmdln.option('--append', action='store_true', help='append to existing value')
@cmdln.option('--clear', action='store_true', help='clear value')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    ${cmd_option_list}

    "accept" will accept all requests in
        $PROJECT:Staging:<LETTER> into $PROJECT
        If openSUSE:* project, requests marked ready from adi stagings will also
        be accepted.

    "acheck" will check if it is safe to accept new staging projects
        As $PROJECT is syncing the right package versions between
        /standard, /totest and /snapshot, it is important that the projects
        are clean prior to a checkin round.

    "adi" will list already staged requests, stage new requests, and supersede
        requests where applicable. New adi stagings will be created for new
        packages based on the grouping options used. The default grouping is by
        source project. When adi stagings are ready the request will be marked
        ready, unstaged, and the adi staging deleted.

    "check" will check if all packages are links without changes

    "check_duplicate_binaries" list binaries provided by multiple packages

    "config" will modify or view staging specific configuration

        Target project level configuration that applies to all stagings can be
        found in the $PROJECT:Staging/dashboard container in file "config". Both
        configuration locations follow the .oscrc format (space separated list).

        config
            Print all staging configuration.
        config key
            Print the value of key for stagings.
        conf key value...
            Set the value of key for stagings.
        config --clear
            Clear all staging configuration.
        config --clear key
            Clear (unset) a single key from staging configuration
        config --append key value...
            Append value to existing value or set if no existing value.

        All of the above may be restricted to a set of stagings.

        The staging configuration is automatically cleared anytime staging
        psuedometa is cleared (accept, or unstage all requests).

        The keys that may be set in staging configuration are:

        - repo_checker-binary-whitelist[-arch]: appended to target project list
        - todo: text to be printed after staging is accepted

    "cleanup_rings" will try to cleanup rings content and print
        out problems

    "freeze" will freeze the sources of the project's links while not
        affecting the source packages

    "frozenage" will show when the respective staging project was last frozen

    "ignore" will ignore a request from "list" and "adi" commands until unignored

    "unignore" will remove from requests from ignore list
        If the --cleanup flag is included then all ignored requests that were
        changed from state new or review more than 3 days ago will be removed.

    "list" will list/supersede requests for ring packages or all if no rings.

    "lock" acquire a hold on the project in order to execute multiple commands
        and prevent others from interrupting. An example:

        lock -m "checkin round"

        list --supersede
        adi
        accept A B C D E

        unlock

        Each command will update the lock to keep it up-to-date.

    "repair" will attempt to repair the state of a request that has been
        corrupted.

        Use the --cleanup flag to include all untracked requests.

    "select" will add requests to the project
        Stagings are expected to be either in short-hand or the full project
        name. For example letter or named stagings can be specified simply as
        A, B, Gcc6, etc, while adi stagings can be specified as adi:1, adi:2,
        etc. Currently, adi stagings are not supported in proposal mode.

        Requests may either be the target package or the request ID.

        When using --filter-by or --group-by the xpath will be applied to the
        request node as returned by OBS. Several values will supplement the
        normal request node.

        - ./action/target/@devel_project: the devel project for the package
        - ./action/target/@ring: the ring to which the package belongs
        - ./@ignored: either false or the provided message

        Some useful examples:

        --filter-by './action/target[starts-with(@package, "yast-")]'
        --filter-by './action/target/[@devel_project="YaST:Head"]'
        --filter-by './action/target[starts-with(@ring, "1")]'
        --filter-by '@id!="1234567"'
        --filter-by 'contains(description, "#Portus")'

        --group-by='./action/target/@devel_project'
        --group-by='./action/target/@ring'

        Multiple filter-by or group-by options may be used at the same time.

        Note that when using proposal mode, multiple stagings to consider may be
        provided in addition to a list of requests by which to filter. A more
        complex example:

        select --group-by='./action/target/@devel_project' A B C 123 456 789

        This will separate the requests 123, 456, 789 by devel project and only
        consider stagings A, B, or C, if available, for placement.

        No arguments is also a valid choice and will propose all non-ignored
        requests into the first available staging. Note that bootstrapped
        stagings are only used when either required or no other stagings are
        available.

        Another useful example is placing all open requests into a specific
        letter staging with:

        select A

        Built in strategies may be specified as well. For example:

        select --strategy devel
        select --strategy quick
        select --strategy special
        select --strategy super

        The default is none and custom is used with any filter-by or group-by
        arguments are provided.

        To merge applicable requests into an existing staging.

        select --merge A

        To automatically try all available strategies.

        select --try-strategies

        These concepts can be combined and interactive mode allows the proposal
        to be modified before it is executed.

    "unselect" will remove from the project - pushing them back to the backlog
        If a message is included the requests will be ignored first.

        Use the --cleanup flag to include all obsolete requests.

    "unlock" will remove the staging lock in case it gets stuck or a manual hold
        If a command lock gets stuck while a hold is placed on a project the
        unlock command will need to be run twice since there are two layers of
        locks.

    "rebuild" will rebuild broken packages in the given stagings or all
        The rebuild command will only trigger builds for packages with less than
        3 failures since the last success or if the build log indicates a stall.

        If the force option is included the rebuild checks will be ignored and
        all packages failing to build will be triggered.

    "supersede" will supersede requests were applicable.
        A request list can be used to limit what is superseded.

    Usage:
        osc staging accept [--force] [--no-cleanup] [LETTER...]
        osc staging acheck
        osc staging adi [--move] [--by-develproject] [--split] [REQUEST...]
        osc staging check [--old] [STAGING...]
        osc staging check_duplicate_binaries
        osc staging config [--append] [--clear] [STAGING...] [key] [value]
        osc staging cleanup_rings
        osc staging freeze [--no-bootstrap] STAGING...
        osc staging frozenage [STAGING...]
        osc staging ignore [-m MESSAGE] REQUEST...
        osc staging unignore [--cleanup] [REQUEST...|all]
        osc staging list [--supersede]
        osc staging lock [-m MESSAGE]
        osc staging select [--no-freeze] [--move [--from STAGING]]
            [--add PACKAGE]
            STAGING REQUEST...
        osc staging select [--no-freeze] [--interactive|--non-interactive]
            [--filter-by...] [--group-by...]
            [--merge] [--try-strategies] [--strategy]
            [STAGING...] [REQUEST...]
        osc staging unselect [--cleanup] [-m MESSAGE] [REQUEST...]
        osc staging unlock
        osc staging rebuild [--force] [STAGING...]
        osc staging repair [--cleanup] [REQUEST...]
        osc staging setprio [STAGING...]
        osc staging supersede [REQUEST...]
    """
    if opts.version:
        self._print_version()

    # verify the argument counts match the commands
    if len(args) == 0:
        raise oscerr.WrongArgs('No command given, see "osc help staging"!')
    cmd = args[0]
    if cmd in (
        'accept',
        'adi',
        'check',
        'config',
        'frozenage',
        'unignore',
        'select',
        'unselect',
        'rebuild',
        'repair',
        'setprio',
        'supersede',
    ):
        min_args, max_args = 0, None
    elif cmd in (
        'freeze',
        'ignore',
    ):
        min_args, max_args = 1, None
    elif cmd in (
        'acheck',
        'check_duplicate_binaries',
        'cleanup_rings',
        'list',
        'lock',
        'unlock',
    ):
        min_args, max_args = 0, 0
    else:
        raise oscerr.WrongArgs('Unknown command: %s' % cmd)
    args = clean_args(args)
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if max_args is not None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # Allow for determining project from osc store.
    if not opts.project:
        if core.is_project_dir('.'):
            opts.project = core.store_read_project('.')
        else:
            opts.project = 'Factory'

    # Init the OBS access and configuration
    opts.project = self._full_project_name(opts.project)
    opts.apiurl = self.get_api_url()
    opts.verbose = False
    config = Config(opts.project)

    colorama.init(autoreset=True,
        strip=(opts.no_color or not bool(int(conf.config.get('staging.color', True)))))
    # Allow colors to be changed.
    for name in dir(Fore):
        if not name.startswith('_'):
            # .oscrc requires keys to be lower-case.
            value = conf.config.get('staging.color.' + name.lower())
            if value:
                setattr(Fore, name, ansi.code_to_chars(value))

    if opts.wipe_cache:
        Cache.delete_all()

    needed = lock_needed(cmd, opts)
    with OBSLock(opts.apiurl, opts.project, reason=cmd, needed=needed) as lock:
        api = StagingAPI(opts.apiurl, opts.project)
        config.apply_remote(api)

        # call the respective command and parse args by need
        if cmd == 'check':
            if len(args) == 1:
                CheckCommand(api).perform(None, opts.old)
            else:
                for prj in args[1:]:
                    CheckCommand(api).perform(prj, opts.old)
                    print()
        elif cmd == 'check_duplicate_binaries':
            CheckDuplicateBinariesCommand(api).perform(opts.save)
        elif cmd == 'config':
            projects = set()
            key = value = None
            stagings = api.get_staging_projects_short(None) + \
                       api.get_staging_projects(include_dvd=False)
            for arg in args[1:]:
                if arg in stagings:
                    projects.add(api.prj_from_short(arg))
                elif key is None:
                    key = arg
                elif value is None:
                    value = arg
                else:
                    value += ' ' + arg

            if not len(projects):
                projects = api.get_staging_projects(include_dvd=False)

            ConfigCommand(api).perform(projects, key, value, opts.append, opts.clear)
        elif cmd == 'freeze':
            for prj in args[1:]:
                prj = api.prj_from_short(prj)
                print(Fore.YELLOW + prj)
                FreezeCommand(api).perform(prj, copy_bootstrap=opts.bootstrap)
        elif cmd == 'frozenage':
            projects = api.get_staging_projects_short() if len(args) == 1 else args[1:]
            for prj in projects:
                prj = api.prj_from_letter(prj)
                print('{} last frozen {}{:.1f} days ago'.format(
                    Fore.YELLOW + prj + Fore.RESET,
                    Fore.GREEN if api.prj_frozen_enough(prj) else Fore.RED,
                    api.days_since_last_freeze(prj)))
        elif cmd == 'acheck':
            # Is it safe to accept? Meaning: /totest contains what it should and is not dirty
            version_totest = api.get_binary_version(api.project, "openSUSE-release.rpm", repository="totest", arch="x86_64")
            if version_totest:
                version_openqa = api.dashboard_content_load('version_totest')
                totest_dirty = api.is_repo_dirty(api.project, 'totest')
                print("version_openqa: %s / version_totest: %s / totest_dirty: %s\n" % (version_openqa, version_totest, totest_dirty))
            else:
                print("acheck is unavailable in %s!\n" % (api.project))
        elif cmd == 'accept':
            # Is it safe to accept? Meaning: /totest contains what it should and is not dirty
            version_totest = api.get_binary_version(api.project, "openSUSE-release.rpm", repository="totest", arch="x86_64")

            if version_totest is None or opts.force:
                # SLE does not have a totest_version or openqa_version - ignore it
                version_openqa = version_totest
                totest_dirty   = False
            else:
                version_openqa = api.dashboard_content_load('version_totest')
                totest_dirty   = api.is_repo_dirty(api.project, 'totest')

            if version_openqa == version_totest and not totest_dirty:
                cmd = AcceptCommand(api)
                for prj in args[1:]:
                    if cmd.perform(api.prj_from_letter(prj), opts.force):
                        cmd.reset_rebuild_data(prj)
                    else:
                        return
                    if not opts.no_cleanup:
                        if api.item_exists(api.prj_from_letter(prj)):
                            cmd.cleanup(api.prj_from_letter(prj))
                        if api.item_exists("%s:DVD" % api.prj_from_letter(prj)):
                            cmd.cleanup("%s:DVD" % api.prj_from_letter(prj))
                cmd.accept_other_new()
                if opts.project.startswith('openSUSE:'):
                    cmd.update_factory_version()
                    if api.item_exists(api.crebuild):
                        cmd.sync_buildfailures()
            else:
                print("Not safe to accept: /totest is not yet synced")
        elif cmd == 'unselect':
            if opts.message:
                print('Ignoring requests first')
                IgnoreCommand(api).perform(args[1:], opts.message)
            UnselectCommand(api).perform(args[1:], opts.cleanup)
        elif cmd == 'select':
            # Include list of all stagings in short-hand and by full name.
            existing_stagings = api.get_staging_projects_short(None)
            existing_stagings += api.get_staging_projects(include_dvd=False)
            stagings = []
            requests = []
            for arg in args[1:]:
                # Since requests may be given by either request ID or package
                # name and stagings may include multi-letter special stagings
                # there is no easy way to distinguish between stagings and
                # requests in arguments. Therefore, check if argument is in the
                # list of short-hand and full project name stagings, otherwise
                # consider it a request. This also allows for special stagings
                # with the same name as package, but the staging will be assumed
                # first time around. The current practice seems to be to start a
                # special staging with a capital letter which makes them unique.
                # lastly adi stagings are consistently prefix with adi: which
                # also makes it consistent to distinguish them from request IDs.
                if arg in existing_stagings and arg not in stagings:
                    stagings.append(api.extract_staging_short(arg))
                elif arg not in requests:
                    requests.append(arg)

            if len(stagings) != 1 or len(requests) == 0 or opts.filter_by or opts.group_by:
                if opts.move or opts.from_:
                    print('--move and --from must be used with explicit staging and request list')
                    return

                open_requests = api.get_open_requests({'withhistory': 1})
                if len(open_requests) == 0:
                    print('No open requests to consider')
                    return

                splitter = RequestSplitter(api, open_requests, in_ring=True)

                considerable = splitter.stagings_load(stagings)
                if considerable == 0:
                    print('No considerable stagings on which to act')
                    return

                if opts.merge:
                    splitter.merge()
                if opts.try_strategies:
                    splitter.strategies_try()
                if len(requests) > 0:
                    splitter.strategy_do('requests', requests=requests)
                if opts.strategy:
                    splitter.strategy_do(opts.strategy)
                elif opts.filter_by or opts.group_by:
                    kwargs = {}
                    if opts.filter_by:
                        kwargs['filters'] = opts.filter_by
                    if opts.group_by:
                        kwargs['groups'] = opts.group_by
                    splitter.strategy_do('custom', **kwargs)
                else:
                    if opts.merge:
                        # Merge any none strategies before final none strategy.
                        splitter.merge(strategy_none=True)
                    splitter.strategy_do('none')
                    splitter.strategy_do_non_bootstrapped('none')

                proposal = splitter.proposal
                if len(proposal) == 0:
                    print('Empty proposal')
                    return

                if opts.interactive:
                    with tempfile.NamedTemporaryFile(suffix='.yml') as temp:
                        temp.write(yaml.safe_dump(splitter.proposal, default_flow_style=False) + '\n\n')

                        if len(splitter.requests):
                            temp.write('# remaining requests:\n')
                            for request in splitter.requests:
                                temp.write('#    {}: {}\n'.format(
                                    request.get('id'), request.find('action/target').get('package')))
                            temp.write('\n')

                        temp.write('# move requests between stagings or comment/remove them\n')
                        temp.write('# change the target staging for a group\n')
                        temp.write('# remove the group, requests, staging, or strategy to skip\n')
                        temp.write('# stagings\n')
                        if opts.merge:
                            temp.write('# - mergeable: {}\n'
                                       .format(', '.join(sorted(splitter.stagings_mergeable +
                                                                splitter.stagings_mergeable_none))))
                        temp.write('# - considered: {}\n'
                                   .format(', '.join(sorted(splitter.stagings_considerable))))
                        temp.write('# - remaining: {}\n'
                                   .format(', '.join(sorted(splitter.stagings_available))))
                        temp.flush()

                        editor = os.getenv('EDITOR')
                        if not editor:
                            editor = 'xdg-open'
                        return_code = subprocess.call(editor.split(' ') + [temp.name])

                        proposal = yaml.safe_load(open(temp.name).read())

                        # Filter invalidated groups from proposal.
                        keys = ['group', 'requests', 'staging', 'strategy']
                        for group, info in sorted(proposal.items()):
                            for key in keys:
                                if not info.get(key):
                                    del proposal[group]
                                    break

                print(yaml.safe_dump(proposal, default_flow_style=False))

                print('Accept proposal? [y/n] (y): ', end='')
                if opts.non_interactive:
                    print('y')
                else:
                    response = raw_input().lower()
                    if response != '' and response != 'y':
                        print('Quit')
                        return

                for group, info in sorted(proposal.items()):
                    print('Staging {} in {}'.format(group, info['staging']))

                    # SelectCommand expects strings.
                    request_ids = map(str, info['requests'].keys())
                    target_project = api.prj_from_short(info['staging'])

                    if 'merge' not in info:
                        # Assume that the original splitter_info is desireable
                        # and that this staging is simply manual followup.
                        api.set_splitter_info_in_prj_pseudometa(target_project, info['group'], info['strategy'])

                    SelectCommand(api, target_project) \
                        .perform(request_ids, no_freeze=opts.no_freeze)
            else:
                target_project = api.prj_from_short(stagings[0])
                if opts.add:
                    api.mark_additional_packages(target_project, [opts.add])
                else:
                    SelectCommand(api, target_project) \
                        .perform(requests, opts.move, opts.from_, opts.no_freeze)
        elif cmd == 'cleanup_rings':
            CleanupRings(api).perform()
        elif cmd == 'ignore':
            IgnoreCommand(api).perform(args[1:], opts.message)
        elif cmd == 'unignore':
            UnignoreCommand(api).perform(args[1:], opts.cleanup)
        elif cmd == 'list':
            ListCommand(api).perform(supersede=opts.supersede)
        elif cmd == 'lock':
            lock.hold(opts.message)
        elif cmd == 'adi':
            AdiCommand(api).perform(args[1:], move=opts.move, by_dp=opts.by_develproject, split=opts.split)
        elif cmd == 'rebuild':
            RebuildCommand(api).perform(args[1:], opts.force)
        elif cmd == 'repair':
            RepairCommand(api).perform(args[1:], opts.cleanup)
        elif cmd == 'setprio':
            PrioCommand(api).perform(args[1:])
        elif cmd == 'supersede':
            SupersedeCommand(api).perform(args[1:])
        elif cmd == 'unlock':
            lock.release(force=True)
