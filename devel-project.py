#!/usr/bin/python

import argparse
import sys
from xml.etree import cElementTree as ET

import osc.conf
import osc.core
from osclib.conf import Config
from osclib.stagingapi import StagingAPI


def devel_projects_get(apiurl, project):
    """
    Returns a sorted list of devel projects for a given project.

    Loads all packages for a given project, checks them for a devel link and
    keeps a list of unique devel projects.
    """
    devel_projects = {}

    url = osc.core.makeurl(apiurl, ['search', 'package'], "match=[@project='%s']" % project)
    root = ET.parse(osc.core.http_GET(url)).getroot()
    for package in root.findall('package'):
        devel = package.find('devel')
        if devel is not None:
            devel_projects[devel.attrib['project']] = True

    return sorted(devel_projects)

def list(args):
    devel_projects = devel_projects_get(osc.conf.config['apiurl'], args.project)
    if len(devel_projects) == 0:
        print('no devel projects found')
    else:
        out = '\n'.join(devel_projects)
        print(out)

        if args.write:
            Config(args.project)
            api = StagingAPI(osc.conf.config['apiurl'], args.project)
            api.save_file_content('%s:Staging' % api.project, 'dashboard', 'devel_projects', out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Operate on devel projects for a given project.')
    subparsers = parser.add_subparsers(title='subcommands')

    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print info useful for debuging')
    parser.add_argument('-p', '--project', default='openSUSE:Factory', metavar='PROJECT', help='project from which to source devel projects')

    parser_list = subparsers.add_parser('list', help='List devel projects.')
    parser_list.set_defaults(func=list)
    parser_list.add_argument('-w', '--write', action='store_true', help='write to dashboard container package')

    args = parser.parse_args()
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug
    sys.exit(args.func(args))
