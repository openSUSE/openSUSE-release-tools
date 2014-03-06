#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import os
import os.path
import re
import sys
from xml.etree import cElementTree as ET

from osc import cmdln, oscerr
from osc.core import delete_project
from osc.core import makeurl
from osc.core import meta_get_packagelist
from osc.core import http_GET
from osc.core import http_POST
from osc.core import server_diff

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)
from osclib.stagingapi import StagingAPI
from osclib.request_finder import RequestFinder

from osclib.select_command import SelectCommand
from osclib.accept_command import AcceptCommand
from osclib.cleanup_rings import CleanupRings
from osclib.list_command import ListCommand


OSC_STAGING_VERSION = '0.0.1'


def _print_version(self):
    """ Print version information about this extension. """
    print(self.OSC_STAGING_VERSION)
    quit(0)

@cmdln.option('-e', '--everything', action='store_true',
              help='during check do not stop on first first issue and show them all')
@cmdln.option('-p', '--parent', metavar='TARGETPROJECT',
              help='manually specify different parent project during creation of staging')
@cmdln.option('-m', '--message', metavar='TEXT',
              help='manually specify different parent project during creation of staging')
@cmdln.option('--move', action='store_true',
              help='force the selection to become a move')
@cmdln.option('-f', '--from', dest='from_', metavar='FROMPROJECT',
              help='manually specify different source project during request moving')
@cmdln.option('-v', '--version', action='store_true',
              help='show version of the plugin')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    "check" will check if all packages are links without changes

    "freeze" will freeze the sources of the project's links (not
        affecting the packages actually in)

    "accept" will accept all requests in
        openSUSE:Factory:Staging:<LETTER> (into Factory)

    "list" will pick the requests not in rings

    "select" will add requests to the project
    "unselect" will remove from the project - pushing them back to the backlog

    Usage:
        osc staging check [--everything] REPO
        osc staging freeze PROJECT
        osc staging list
        osc staging select [--move [-from PROJECT]] LETTER REQUEST...
        osc staging unselect REQUEST...
        osc staging accept LETTER
        osc staging cleanup_rings
    """
    if opts.version:
        self._print_version()

    # verify the argument counts match the commands
    if len(args) == 0:
        raise oscerr.WrongArgs('No command given, see "osc help staging"!')
    cmd = args[0]
    if cmd in ('submit-devel', 's', 'remove', 'r', 'accept', 'freeze'):
        min_args, max_args = 1, 1
    elif cmd == 'check':
        min_args, max_args = 0, 2
    elif cmd == 'select':
        min_args, max_args = 2, None
    elif cmd == 'unselect':
        min_args, max_args = 1, None
    elif cmd in ('list', 'cleanup_rings'):
        min_args, max_args = 0, 0
    else:
        raise oscerr.WrongArgs('Unknown command: %s' % cmd)
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if not max_args is None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # init the obs access
    opts.apiurl = self.get_api_url()
    opts.verbose = False

    api = StagingAPI(opts.apiurl)

    # call the respective command and parse args by need
    if cmd in ['check']:
        # FIXME: de-duplicate and use function when cleaning up this file
        if len(args) > 1:
            prj = api.prj_from_letter(args[1])
            state = api.check_project_status(prj, True)

            # If the state is green we do nothing
            if not state:
                print('Skipping empty staging project: {}'.format(prj))
                print('')
                return True

            print('Checking staging project: {}'.format(prj))
            if type(state) is list:
                print(' -- Project still neeeds attention')
                for i in state:
                    print(i)
            else:
                print(' ++ Acceptable staging project')

            return True

        for prj in api.get_staging_projects():
            state = api.check_project_status(prj)

            # If the state is green we do nothing
            if not state:
                print('Skipping empty staging project: {}'.format(prj))
                print('')
                continue

            print('Checking staging project: {}'.format(prj))
            if type(state) is list:
                print(' -- Project still neeeds attention')
                for i in state:
                    print(i)
            else:
                print(' ++ Acceptable staging project')
            print('')
        return True
    elif cmd == 'freeze':
        import osclib.freeze_command
        for prj in args[1:]:
            osclib.freeze_command.FreezeCommand(api).perform(api. prj_from_letter(prj))
    elif cmd == 'accept':
        return AcceptCommand(api).perform(api. prj_from_letter(args[1]))
    elif cmd == 'unselect':
        for rq, rq_prj in RequestFinder.find_staged_sr(args[1:], opts.apiurl, api).items():
            print('Unselecting "{}" from "{}"'.format(rq, rq_prj['staging']))
            api.rm_from_prj(rq_prj['staging'], request_id=rq)
            api.add_review(rq, by_group='factory-staging', msg='Please recheck')
    elif cmd == 'select':
        tprj = api.prj_from_letter(args[1])
        return SelectCommand(api).perform(tprj, args[2:], opts.move, opts.from_)
    elif cmd == 'cleanup_rings':
        return CleanupRings(opts.apiurl).perform()
    elif cmd == 'list':
        return ListCommand(api).perform()
