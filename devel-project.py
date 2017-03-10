#!/usr/bin/python

import argparse
import sys
from xml.etree import cElementTree as ET

import osc.conf
from osc.core import get_request_list
from osc.core import get_review_list
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

def requests(args):
    apiurl = osc.conf.config['apiurl']
    devel_projects = devel_projects_load(args)

    for devel_project in devel_projects:
        requests = get_request_list(apiurl, devel_project,
                                    req_state=('new', 'review'),
                                    req_type='submit',
                                    # Seems to work backwards, as it includes only.
                                    exclude_target_projects=[devel_project])
        for request in requests:
            action = request.actions[0]
            print(' '.join((
                request.reqid,
                '/'.join((action.tgt_project, action.tgt_package)),
                '/'.join((action.src_project, action.src_package)),
            )))

def reviews(args):
    apiurl = osc.conf.config['apiurl']
    devel_projects = devel_projects_load(args)

    for devel_project in devel_projects:
        requests = get_review_list(apiurl, byproject=devel_project)
        for request in requests:
            action = request.actions[0]
            if action.type != 'submit':
                continue

            for review in request.reviews:
                if review.by_project == devel_project:
                    break

            print(' '.join((
                request.reqid,
                '/'.join((review.by_project, review.by_package)) if review.by_package else review.by_project,
                '/'.join((action.tgt_project, action.tgt_package)),
            )))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Operate on devel projects for a given project.')
    subparsers = parser.add_subparsers(title='subcommands')

    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print info useful for debuging')
    parser.add_argument('-p', '--project', default='openSUSE:Factory', metavar='PROJECT', help='project from which to source devel projects')

    parser_list = subparsers.add_parser('list', help='List devel projects.')
    parser_list.set_defaults(func=list)
    parser_list.add_argument('-w', '--write', action='store_true', help='write to dashboard container package')

    parser_requests = subparsers.add_parser('requests', help='List open requests.')
    parser_requests.set_defaults(func=requests)

    parser_reviews = subparsers.add_parser('reviews', help='List open reviews.')
    parser_reviews.set_defaults(func=reviews)

    args = parser.parse_args()
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug
    sys.exit(args.func(args))
