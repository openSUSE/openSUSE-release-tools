#!/usr/bin/python

import argparse
from datetime import datetime
import dateutil.parser
import sys
from xml.etree import cElementTree as ET

import osc.conf
from osc.core import HTTPError
from osc.core import get_request_list
from osc.core import get_review_list
from osc.core import http_GET
from osc.core import makeurl
from osc.core import show_package_meta
from osc.core import show_project_meta
from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.stagingapi import StagingAPI


BOT_NAME = 'devel-project'
REMINDER = 'review reminder'

# Short of either copying the two osc.core list functions to build the search
# queries and call a different search function this is the only reasonable way
# to add withhistory to the query. The base search function does not even have a
# method for adding to the query. Alternatively, get_request() can be called for
# each request to load the history, but obviously that is not very desirable.
# Having the history allows for the age of the request to be determined.
def search(apiurl, **kwargs):
    res = {}
    for urlpath, xpath in kwargs.items():
        path = [ 'search' ]
        path += urlpath.split('_')
        query = {'match': xpath}
        if urlpath == 'request':
            query['withhistory'] = 1
        u = makeurl(apiurl, path, query)
        f = http_GET(u)
        res[urlpath] = ET.parse(f).getroot()
    return res

osc.core._search = osc.core.search
osc.core.search = search

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

    # Ensure self does not end up in list.
    del devel_projects[project]

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

def request_age(request):
    date = dateutil.parser.parse(request.statehistory[0].when)
    delta = datetime.utcnow() - date
    return delta.days

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
            age = request_age(request)
            if age < args.min_age:
                continue

            print(' '.join((
                request.reqid,
                '/'.join((action.tgt_project, action.tgt_package)),
                '/'.join((action.src_project, action.src_package)),
                '({} days old)'.format(age),
            )))

            if args.remind:
                remind_comment(apiurl, args.repeat_age, request.reqid, action.tgt_project, action.tgt_package)

def reviews(args):
    apiurl = osc.conf.config['apiurl']
    devel_projects = devel_projects_load(args)

    for devel_project in devel_projects:
        requests = get_review_list(apiurl, byproject=devel_project)
        for request in requests:
            action = request.actions[0]
            if action.type != 'submit':
                continue

            age = request_age(request)
            if age < args.min_age:
                continue

            for review in request.reviews:
                if review.by_project == devel_project:
                    break

            print(' '.join((
                request.reqid,
                '/'.join((review.by_project, review.by_package)) if review.by_package else review.by_project,
                '/'.join((action.tgt_project, action.tgt_package)),
                '({} days old)'.format(age),
            )))

            if args.remind:
                remind_comment(apiurl, args.repeat_age, request.reqid, review.by_project, review.by_package)

def maintainers_get(apiurl, project, package=None):
    if package:
        try:
            meta = show_package_meta(apiurl, project, package)
        except HTTPError as e:
            if e.code == 404:
                # Fallback to project in the case of new package.
                meta = show_project_meta(apiurl, project)
    else:
        meta = show_project_meta(apiurl, project)
    meta = ET.fromstring(''.join(meta))

    userids = []
    for person in meta.findall('person[@role="maintainer"]'):
        userids.append(person.get('userid'))

    if len(userids) == 0 and package is not None:
        # Fallback to project if package has no maintainers.
        return maintainers_get(apiurl, project)

    return userids

def remind_comment(apiurl, repeat_age, request_id, project, package=None):
    comment_api = CommentAPI(apiurl)
    comments = comment_api.get_comments(request_id=request_id)
    comment, _ = comment_api.comment_find(comments, BOT_NAME)

    if comment:
        delta = datetime.utcnow() - comment['when']
        if delta.days < repeat_age:
            print('  skipping due to previous reminder from {} days ago'.format(delta.days))
            return

        # Repeat notification so remove old comment.
        try:
            comment_api.delete(comment['id'])
        except HTTPError, e:
            if e.code == 403:
                # Gracefully skip when previous reminder was by another user.
                print('  unable to remove previous reminder')
                return
            raise e

    userids = sorted(maintainers_get(apiurl, project, package))
    if len(userids):
        users = ['@' + userid for userid in userids]
        message = '{}: {}'.format(', '.join(users), REMINDER)
    else:
        message = REMINDER
    print('  ' + message)
    message = comment_api.add_marker(message, BOT_NAME)
    comment_api.add_comment(request_id=request_id, comment=message)

def common_args_add(parser):
    parser.add_argument('--min-age', type=int, default=0, metavar='DAYS', help='min age of requests')
    parser.add_argument('--repeat-age', type=int, default=7, metavar='DAYS', help='age after which a new reminder will be sent')
    parser.add_argument('--remind', action='store_true', help='remind maintainers to review')


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
    common_args_add(parser_requests)

    parser_reviews = subparsers.add_parser('reviews', help='List open reviews.')
    parser_reviews.set_defaults(func=reviews)
    common_args_add(parser_reviews)

    args = parser.parse_args()
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug
    sys.exit(args.func(args))
