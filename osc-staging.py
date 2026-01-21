import os
import os.path
import subprocess
import tempfile
import warnings
import yaml

try:
    import __builtin__
    input = getattr(__builtin__, 'raw_input')
except (ImportError, AttributeError):
    pass

from osc import cmdln


def _print_version(self):
    from osclib.common import VERSION
    print(VERSION)
    quit(0)


def _full_project_name(self, project):
    """Deduce the full project name."""
    if project.startswith(('openSUSE', 'SUSE')):
        return project

    if project.startswith('Factory'):
        return f'openSUSE:{project}'

    if project.startswith('SLE') or project.startswith('ALP'):
        return f'SUSE:{project}'

    # If we can't guess, raise a Warning
    if (':' not in project):
        warnings.warn(f'{project} project not recognized.')
    return project


def lock_needed(cmd, opts):
    return not (
        cmd in ('check', 'check_duplicate_binaries', 'check_local_links',
                'frozenage', 'rebuild', 'unlock', 'setprio', 'cleanup_rings') or
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
@cmdln.option('--split', action='store_true',
              help='split the requests into individual groups')
@cmdln.option('--supersede', action='store_true',
              help='replace staged requests when superseded')
@cmdln.option('--adi-details', action='store_true',
              help='show detailed summary for packages that are not in any ring')
@cmdln.option('--filter-from', metavar='STAGING',
              help='filter request list to only those from a specific staging')
@cmdln.option('-p', '--project', dest='project', metavar='PROJECT',
              help='indicate the project on which to operate, default is openSUSE:Factory')
@cmdln.option('--force', action='store_true',
              help='force action, overruling internal checks (CAUTION)')
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
@cmdln.option('--match-filter', help='xpath by which to filter requests on the basis of the reviews they received', default=None)
@cmdln.option('--no-color', action='store_true', help='strip colors from output (or add staging.color = 0 to the .oscrc general section')
@cmdln.option('--remove-exclusion', action='store_true', help='unignore selected requests automatically', default=False)
@cmdln.option('--save', action='store_true', help='save the result to the pseudometa package')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    ${cmd_option_list}

    "accept" will accept all requests in the given stagings. Without argument,
        it accepts all acceptable stagings.

    "adi" will list already staged requests, stage new requests, and supersede
        requests where applicable. New adi stagings will be created for new
        packages based on the grouping options used. The default grouping is by
        source project. When adi stagings are empty, they are deleted.

    "check" will check if all packages are links without changes

    "check_local_links" lists local links that don't match multispec package

    "check_duplicate_binaries" list binaries provided by multiple packages

    "cleanup_rings" will try to cleanup rings content and print
        out problems

    "rebase" (or "freeze") will freeze the sources of the project's links while
        not affecting the source packages

    "frozenage" will show when the respective staging project was last frozen

    "ignore" will ignore a request from "list" and "adi" commands until unignored

    "unignore" will remove from requests from ignore list
        If the --cleanup flag is included then all ignored requests that were
        changed from state new or review more than 3 days ago will be removed.

    "list" will list/supersede requests for ring packages or all if no rings.

        By just calling list, the staging plugin will list all the request included
        in the backlog section of the web UI. It is also possible to optionally limit
        results with an XPATH filter. As an example, the following would list all
        packages which have received a positive review from a member of the
        licensedigger group or the factory-auto one

        list --match-filter "state/@name='review' and review[(@by_group='factory-auto' or @by_group='licensedigger') and @state='accepted']"

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
        request node as returned by OBS. Use the following on a current request
        to see the XML structure.

        osc api /request/1337

        A number of additional values will supplement the normal request node.

        - ./action/target/@devel_project: the devel project for the package
        - ./action/target/@devel_project_super: super devel project if relevant
        - ./action/target/@ring: the ring to which the package belongs
        - ./@aged: either True or False based on splitter-request-age-threshold
        - ./@ignored: either False or the provided message

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

        Moving requests can be accomplished using the --move flag. For example,
        to move already staged pac1 and pac2 to staging B use the following.

        select --move B pac1 pac2

        The staging in which the requests are staged will automatically be
        determined and the requests will be removed from that staging and placed
        in the specified staging.

        Related to this, the --filter-from option may be used in conjunction
        with --move to only move requests already staged in a specific staging.
        This can be useful if a staging master is responsible for a specific set
        of packages and wants to move them into a different staging when they
        were already placed in a mixed staging. For example, if one had a file
        with a list of packages the following would move any of them found in
        staging A to staging B.

        select --move --filter-from A B $(< package.list)

        select --remove-exclusion will unignore the requests selected (ignored requests
        are called excluded in the OBS API)

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

    "setprio" will set priority of requests withing stagings
        If no stagings are specified all stagings will be used.
        The default priority is important, but the possible values are:
          "critical", "important", "moderate" or "low".

    "supersede" will supersede requests were applicable.
        A request list can be used to limit what is superseded.

    Usage:
        osc staging accept [--force] [--no-cleanup] [STAGING...]
        osc staging adi [--move] [--split] [REQUEST...]
        osc staging check [STAGING...]
        osc staging check_duplicate_binaries
        osc staging check_local_links
        osc staging cleanup_rings
        osc staging rebase|freeze [--no-bootstrap] STAGING...
        osc staging frozenage [STAGING...]
        osc staging ignore [-m MESSAGE] REQUEST...
        osc staging unignore [--cleanup] [REQUEST...|all]
        osc staging list [--adi-details] [--match-filter] [--supersede]
        osc staging lock [-m MESSAGE]
        osc staging select [--no-freeze] [--remove-exclusion] [--move [--filter-from STAGING]]
            STAGING REQUEST...
        osc staging select [--no-freeze] [--interactive|--non-interactive]
            [--filter-by...] [--group-by...]
            [--merge] [--try-strategies] [--strategy]
            [STAGING...] [REQUEST...]
        osc staging unselect [--cleanup] [-m MESSAGE] [REQUEST...]
        osc staging unlock
        osc staging rebuild [--force] [STAGING...]
        osc staging repair [--cleanup] [REQUEST...]
        osc staging setprio [STAGING...] [priority]
        osc staging supersede [REQUEST...]
    """
    import colorama  # pylint: disable=import-outside-toplevel
    from colorama import Fore  # pylint: disable=import-outside-toplevel
    from colorama import ansi  # pylint: disable=import-outside-toplevel

    from osc import conf  # pylint: disable=import-outside-toplevel
    from osc import core  # pylint: disable=import-outside-toplevel
    from osc import oscerr  # pylint: disable=import-outside-toplevel

    from osclib.accept_command import AcceptCommand  # pylint: disable=import-outside-toplevel
    from osclib.adi_command import AdiCommand  # pylint: disable=import-outside-toplevel
    from osclib.check_command import CheckCommand  # pylint: disable=import-outside-toplevel
    from osclib.check_duplicate_binaries_command import CheckDuplicateBinariesCommand  # pylint: disable=import-outside-toplevel
    from osclib.cleanup_rings import CleanupRings  # pylint: disable=import-outside-toplevel
    from osclib.conf import Config  # pylint: disable=import-outside-toplevel
    from osclib.freeze_command import FreezeCommand  # pylint: disable=import-outside-toplevel
    from osclib.ignore_command import IgnoreCommand  # pylint: disable=import-outside-toplevel
    from osclib.unignore_command import UnignoreCommand  # pylint: disable=import-outside-toplevel
    from osclib.list_command import ListCommand  # pylint: disable=import-outside-toplevel
    from osclib.obslock import OBSLock  # pylint: disable=import-outside-toplevel
    from osclib.select_command import SelectCommand  # pylint: disable=import-outside-toplevel
    from osclib.stagingapi import StagingAPI  # pylint: disable=import-outside-toplevel
    from osclib.cache import Cache  # pylint: disable=import-outside-toplevel
    from osclib.unselect_command import UnselectCommand  # pylint: disable=import-outside-toplevel
    from osclib.repair_command import RepairCommand  # pylint: disable=import-outside-toplevel
    from osclib.rebuild_command import RebuildCommand  # pylint: disable=import-outside-toplevel
    from osclib.request_splitter import RequestSplitter  # pylint: disable=import-outside-toplevel
    from osclib.supersede_command import SupersedeCommand  # pylint: disable=import-outside-toplevel
    from osclib.prio_command import PrioCommand  # pylint: disable=import-outside-toplevel

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
        'supersede',
    ):
        min_args, max_args = 0, None
    elif cmd in (
        'freeze',
        'rebase',
        'setprio',
        'ignore',
    ):
        min_args, max_args = 1, None
    elif cmd in (
        'check_duplicate_binaries',
        'check_local_links',
        'cleanup_rings',
        'list',
        'lock',
        'unlock',
    ):
        min_args, max_args = 0, 0
    else:
        raise oscerr.WrongArgs(f'Unknown command: {cmd}')
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

    # Cache the remote config fetch.
    Cache.init()

    # Init the OBS access and configuration
    opts.project = self._full_project_name(opts.project)
    opts.apiurl = self.get_api_url()
    opts.verbose = False
    Config(opts.apiurl, opts.project)

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

    api = StagingAPI(opts.apiurl, opts.project)
    needed = lock_needed(cmd, opts)
    with OBSLock(opts.apiurl, opts.project, reason=cmd, needed=needed) as lock:

        # call the respective command and parse args by need
        if cmd == 'check':
            if len(args) == 1:
                CheckCommand(api).perform(None)
            else:
                for prj in args[1:]:
                    CheckCommand(api).perform(prj)
                    print()
        elif cmd == 'check_duplicate_binaries':
            CheckDuplicateBinariesCommand(api).perform(opts.save)
        elif cmd == 'check_local_links':
            AcceptCommand(api).check_local_links()
        elif cmd == 'freeze' or cmd == 'rebase':
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
        elif cmd == 'accept':
            cmd = AcceptCommand(api)
            cmd.accept_all(args[1:], opts.force, not opts.no_cleanup)
        elif cmd == 'unselect':
            UnselectCommand(api).perform(args[1:], opts.cleanup, opts.message)
        elif cmd == 'select':
            # Include list of all stagings in short-hand and by full name.
            existing_stagings = api.get_staging_projects_short(None)
            existing_stagings += api.get_staging_projects()
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
                #
                # also support --move passing 2 or more staging projects to merge
                if arg in existing_stagings and arg not in stagings and not (len(stagings) > 0 and opts.move):
                    stagings.append(api.extract_staging_short(arg))
                elif arg not in requests:
                    requests.append(arg)

            if len(stagings) != 1 or len(requests) == 0 or opts.filter_by or opts.group_by:
                if opts.move or opts.filter_from:
                    print('--move and --filter-from must be used with explicit staging and request list')
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
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.yml') as temp:
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
                        subprocess.call(editor.split(' ') + [temp.name])

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
                    response = input().lower()
                    if response != '' and response != 'y':
                        print('Quit')
                        return

                for group, info in sorted(proposal.items()):
                    print(f"Staging {group} in {info['staging']}")

                    # SelectCommand expects strings.
                    request_ids = map(str, info['requests'].keys())
                    target_project = api.prj_from_short(info['staging'])

                    # TODO: Find better place for splitter info
                    # if 'merge' not in info:
                    #    Assume that the original splitter_info is desireable
                    #    and that this staging is simply manual followup.
                    #    api.set_splitter_info_in_prj_pseudometa(target_project, info['group'], info['strategy'])

                    SelectCommand(api, target_project) \
                        .perform(request_ids, no_freeze=opts.no_freeze, remove_exclusion=opts.remove_exclusion)
            else:
                target_project = api.prj_from_short(stagings[0])
                filter_from = api.prj_from_short(opts.filter_from) if opts.filter_from else None
                SelectCommand(api, target_project) \
                    .perform(requests, opts.move,
                             filter_from, no_freeze=opts.no_freeze, remove_exclusion=opts.remove_exclusion)
        elif cmd == 'cleanup_rings':
            CleanupRings(api).perform()
        elif cmd == 'ignore':
            IgnoreCommand(api).perform(args[1:], opts.message)
        elif cmd == 'unignore':
            UnignoreCommand(api).perform(args[1:], opts.cleanup)
        elif cmd == 'list':
            ListCommand(api).perform(adi_details=opts.adi_details, match_filter=opts.match_filter, supersede=opts.supersede)
        elif cmd == 'lock':
            lock.hold(opts.message)
        elif cmd == 'adi':
            AdiCommand(api).perform(args[1:], move=opts.move, split=opts.split)
        elif cmd == 'rebuild':
            RebuildCommand(api).perform(args[1:], opts.force)
        elif cmd == 'repair':
            RepairCommand(api).perform(args[1:], opts.cleanup)
        elif cmd == 'setprio':
            stagings = []
            priority = None

            priorities = ['critical', 'important', 'moderate', 'low']
            for arg in args[1:]:
                if arg in priorities:
                    priority = arg
                else:
                    stagings.append(arg)

            PrioCommand(api).perform(stagings, priority)
        elif cmd == 'supersede':
            SupersedeCommand(api).perform(args[1:])
        elif cmd == 'unlock':
            lock.release(force=True)
