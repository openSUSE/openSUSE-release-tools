#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
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


def _get_changed(opts, project, everything):
    ret = []
    # Check for local changes
    for pkg in meta_get_packagelist(opts.apiurl, project):
        if len(ret) != 0 and not everything:
            break
        f = http_GET(makeurl(opts.apiurl, ['source', project, pkg]))
        linkinfo = ET.parse(f).getroot().find('linkinfo')
        if linkinfo is None:
            ret.append({'pkg': pkg, 'code': 'NOT_LINK',
                        'msg': 'Not a source link'})
            continue
        if linkinfo.get('error'):
            ret.append({'pkg': pkg, 'code': 'BROKEN',
                        'msg': 'Broken source link'})
            continue
        t = linkinfo.get('project')
        p = linkinfo.get('package')
        r = linkinfo.get('revision')
        if server_diff(opts.apiurl, t, p, r, project, pkg, None, True):
            ret.append({
                'pkg': pkg,
                'code': 'MODIFIED',
                'msg': 'Has local modifications',
                'pprj': t,
                'ppkg': p
            })
            continue
    return ret


def _staging_remove(self, project, opts):
    """
    Remove staging project.
    :param project: staging project to delete
    :param opts: pointer to options
    """
    chng = _get_changed(opts, project, True)
    if len(chng) > 0:
        print('Staging project "%s" is not clean:' % project)
        print('')
        for pair in chng:
            print(' * %s : %s' % (pair['pkg'], pair['msg']))
        print('')
        print('Really delete? (N/y)')
        answer = sys.stdin.readline()
        if not re.search("^\s*[Yy]", answer):
            print('Aborting...')
            exit(1)
    delete_project(opts.apiurl, project, force=True, msg=None)
    print("Deleted.")
    return


def _staging_submit_devel(self, project, opts):
    """
    Generate new review requests for devel-projects based on our
    staging changes.
    :param project: staging project to submit into devel projects
    """
    chng = _get_changed(opts, project, True)
    msg = "Fixes from staging project %s" % project
    if opts.message is not None:
        msg = opts.message
    if len(chng) > 0:
        for pair in chng:
            if pair['code'] != 'MODIFIED':
                print('Error: Package "%s": %s' % (pair['pkg'], pair['msg']))
            else:
                print('Sending changes back %s/%s -> %s/%s' % (project, pair['pkg'], pair['pprj'], pair['ppkg']))
                action_xml = '<request>'
                action_xml += '   <action type="submit"> <source project="%s" package="%s" /> <target project="%s" package="%s" />' % (project, pair['pkg'], pair['pprj'], pair['ppkg'])
                action_xml += '   </action>'
                action_xml += '   <state name="new"/> <description>%s</description>' % msg
                action_xml += '</request>'

                u = makeurl(opts.apiurl, ['request'],
                            query='cmd=create&addrevision=1')
                f = http_POST(u, data=action_xml)

                root = ET.parse(f).getroot()
                print("Created request %s" % (root.get('id')))
    else:
        print('No changes to submit')
    return


@cmdln.option('-c', '--commit', action='store_true',
              help='accept the request completely and commit the changes to the openSUSE:Factory')
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

    "remove" (or "r") will delete the staging project into submit
        requests for openSUSE:Factory

    "submit-devel" (or "s") will create review requests for changed
        packages in staging project into their respective devel
        projects to obtain approval from maitnainers for pushing the
        changes to openSUSE:Factory

    "freeze" will freeze the sources of the project's links (not
        affecting the packages actually in)

    "accept" will accept all requests in
        openSUSE:Factory:Staging:<LETTER> (into Factory)

    "list" will pick the requests not in rings

    "select" will add requests to the project
    "unselect" will remove them project - pushing them back to the backlog

    Usage:
        osc staging check [--everything] REPO
        osc staging remove REPO
        osc staging submit-devel [-m message] REPO
        osc staging freeze PROJECT
        osc staging list
        osc staging select [--move [-from PROJECT]] LETTER REQUEST...
        osc staging unselect LETTER REQUEST...
        osc staging accept [--commit] LETTER
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
    elif cmd in ('remove', 'r'):
        project = args[1]
        self._staging_remove(project, opts)
    elif cmd in ('submit-devel', 's'):
        project = args[1]
        self._staging_submit_devel(project, opts)
    elif cmd == 'freeze':
        import osclib.freeze_command
        for prj in args[1:]:
            osclib.freeze_command.FreezeCommand(api).perform(api. prj_from_letter(prj))
    elif cmd == 'accept':
        return AcceptCommand(api).perform(api. prj_from_letter(args[1]), opts.commit)
    elif cmd == 'unselect':
        for rq_or_pkg in args[1:]:
            rq, rq_prj = RequestFinder.find_single_sr(rq_or_pkg, opts.apiurl)
            if 'staging' in rq_prj:
                print('Unselecting "{}" from "{}"'.format(rq_or_pkg, rq_prj['staging']))
                api.rm_from_prj(rq_prj['staging'], request_id=rq)
                api.add_review(rq, by_group='factory-staging',
                               msg='Please recheck')
            else:
                print('Can\'t unselect "{}" because is not in any staging project'.format(rq_or_pkg))
    elif cmd == 'select':
        tprj = api.prj_from_letter(args[1])
        return SelectCommand(api).perform(tprj, args[2:], opts.move, opts.from_)
    elif cmd == 'cleanup_rings':
        return CleanupRings(opts.apiurl).perform()
    elif cmd == 'list':
        return ListCommand(api).perform()
