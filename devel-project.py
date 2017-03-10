#!/usr/bin/python

import argparse
import sys
from xml.etree import cElementTree as ET

import osc.conf
from osc.core import http_GET
from osc.core import makeurl
from osclib.conf import Config
from osclib.stagingapi import StagingAPI


def staging_api(args):
    Config(args.project)
    api = StagingAPI(osc.conf.config['apiurl'], args.project)
    staging = '%s:Staging' % api.project
    return (api, staging)

def devel_projects_get(apiurl, project):
    """
    Returns a sorted list of devel projects for a given project.

    Loads all packages for a given project, checks them for a devel link and
    keeps a list of unique devel projects.
    """
    devel_projects = {}

    url = makeurl(apiurl, ['search', 'package'], "match=[@project='%s']" % project)
    root = ET.parse(http_GET(url)).getroot()
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
            api, staging = staging_api(args)
            if api.load_file_content(staging, 'dashboard', 'devel_projects') != out:
                api.save_file_content(staging, 'dashboard', 'devel_projects', out)

def devel_projects_load(args):
    api, staging = staging_api(args)
    devel_projects = api.load_file_content(staging, 'dashboard', 'devel_projects')

    if devel_projects:
        return devel_projects.splitlines()

    raise Exception('no devel projects found')


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
