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

import os
import os.path
import sys

from osc import cmdln
from osc import oscerr

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)
from osclib.accept_command import AcceptCommand
from osclib.check_command import CheckCommand
from osclib.cleanup_rings import CleanupRings
from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.list_command import ListCommand
from osclib.obslock import OBSLock
from osclib.select_command import SelectCommand
from osclib.stagingapi import StagingAPI
from osclib.unselect_command import UnselectCommand

OSC_STAGING_VERSION = '0.0.1'


def _print_version(self):
    """ Print version information about this extension. """
    print(self.OSC_STAGING_VERSION)
    quit(0)


def _full_project_name(self, project):
    """Deduce the full project name."""
    if ':' in project:
        return project

    if 'Factory' in project or 'openSUSE' in project:
        return 'openSUSE:%s' % project

    if 'SLE' in project:
        return 'SUSE:%s' % project

    # If we can't guess, raise a Warning
    raise Warning('% project not recognized.' % project)
    return project


@cmdln.option('--move', action='store_true',
              help='force the selection to become a move')
@cmdln.option('-f', '--from', dest='from_', metavar='FROMPROJECT',
              help='manually specify different source project during request moving')
@cmdln.option('-p', '--project', dest='project', metavar='PROJECT', default='Factory',
              help='select a different project instead of openSUSE:Factory')
@cmdln.option('--add', dest='add', metavar='PACKAGE',
              help='mark additional packages to be checked by repo checker')
@cmdln.option('-o', '--old', action='store_true',
              help='use the old check algorithm')
@cmdln.option('-v', '--version', action='store_true',
              help='show version of the plugin')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    "accept" will accept all requests in
        openSUSE:Factory:Staging:<LETTER> (into Factory)

    "check" will check if all packages are links without changes

    "cleanup_rings" will try to cleanup rings content and print
        out problems

    "freeze" will freeze the sources of the project's links (not
        affecting the packages actually in)

    "list" will pick the requests not in rings

    "select" will add requests to the project

    "unselect" will remove from the project - pushing them back to the backlog

    Usage:
        osc staging accept LETTER
        osc staging check [--old] REPO
        osc staging cleanup_rings
        osc staging freeze PROJECT...
        osc staging list
        osc staging select [--move [--from PROJECT]] LETTER REQUEST...
        osc staging unselect REQUEST...
    """
    if opts.version:
        self._print_version()

    # verify the argument counts match the commands
    if len(args) == 0:
        raise oscerr.WrongArgs('No command given, see "osc help staging"!')
    cmd = args[0]
    if cmd in ('accept', 'freeze'):
        min_args, max_args = 1, None
    elif cmd == 'check':
        min_args, max_args = 0, 2
    elif cmd == 'select':
        min_args, max_args = 1, None
        if not opts.add:
            min_args = 2
    elif cmd == 'unselect':
        min_args, max_args = 1, None
    elif cmd in ('list', 'cleanup_rings'):
        min_args, max_args = 0, 0
    else:
        raise oscerr.WrongArgs('Unknown command: %s' % cmd)
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if max_args is not None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # Init the OBS access and configuration
    opts.project = self._full_project_name(opts.project)
    opts.apiurl = self.get_api_url()
    opts.verbose = False
    Config(opts.project)

    with OBSLock(opts.apiurl, opts.project):
        api = StagingAPI(opts.apiurl, opts.project)

        # call the respective command and parse args by need
        if cmd == 'check':
            prj = args[1] if len(args) > 1 else None
            CheckCommand(api).perform(prj, opts.old)
        elif cmd == 'freeze':
            for prj in args[1:]:
                FreezeCommand(api).perform(api.prj_from_letter(prj))
        elif cmd == 'accept':
            cmd = AcceptCommand(api)
            for prj in args[1:]:
                if not cmd.perform(api.prj_from_letter(prj)):
                    return
            cmd.accept_other_new()
            cmd.update_factory_version()
            cmd.sync_buildfailures()
        elif cmd == 'unselect':
            UnselectCommand(api).perform(args[1:])
        elif cmd == 'select':
            tprj = api.prj_from_letter(args[1])
            if opts.add:
                api.mark_additional_packages(tprj, [opts.add])
            else:
                SelectCommand(api).perform(tprj, args[2:], opts.move, opts.from_)
        elif cmd == 'cleanup_rings':
            CleanupRings(api).perform()
        elif cmd == 'list':
            ListCommand(api).perform()
