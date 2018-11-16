#!/usr/bin/python

from copy import deepcopy
from lxml import etree as ET
from osc.core import copy_pac as copy_package
from osc.core import get_commitlog
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT
from osc.core import makeurl
from osc.core import show_upstream_rev
from osclib.core import project_pseudometa_package

try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

import argparse
import osc.conf
import sys


def project_fence(project):
    if ((project.startswith('openSUSE:') and project_fence.project.startswith('openSUSE:')) and
        not project.startswith(project_fence.project)):
        # Exclude other openSUSE:* projects while cloning a specifc one.
        return False
    if project.startswith('openSUSE:Factory:ARM'):
        # Troublesome.
        return False
    # Perhaps use devel project list as filter, but for now quick exclude.
    if project.startswith('SUSE:') or project.startswith('Ubuntu:'):
        return False

    return True

def entity_clone(apiurl_source, apiurl_target, path, sanitize=None, clone=None, after=None):
    if not hasattr(entity_clone, 'cloned'):
        entity_clone.cloned = []

    if path[0] == 'source' and not project_fence(path[1]):
        # Skip projects outside of fence by marking as cloned.
        if path not in entity_clone.cloned:
            entity_clone.cloned.append(path)

    if path in entity_clone.cloned:
        print('skip {}'.format('/'.join(path)))
        return

    print('clone {}'.format('/'.join(path)))
    entity_clone.cloned.append(path)

    url = makeurl(apiurl_source, path)
    entity = ET.parse(http_GET(url)).getroot()

    if sanitize:
        sanitize(entity)
    if clone:
        clone(apiurl_source, apiurl_target, entity)

    url = makeurl(apiurl_target, path)
    http_PUT(url, data=ET.tostring(entity))

    if after:
        after(apiurl_source, apiurl_target, entity)

def users_clone(apiurl_source, apiurl_target, entity):
    for person in entity.findall('person'):
        path = ['person', person.get('userid')]
        entity_clone(apiurl_source, apiurl_target, path, person_sanitize, after=person_clone_after)

    for group in entity.findall('group'):
        path = ['group', group.get('groupid')]
        entity_clone(apiurl_source, apiurl_target, path, clone=group_clone)

def project_references_remove(project):
    # Remove links that reference other projects.
    for link in project.xpath('link[@project]'):
        link.getparent().remove(link)

    # Remove repositories that reference other projects.
    for repository in project.xpath('repository[releasetarget or path]'):
        repository.getparent().remove(repository)

# clone(Factory)
# - stripped
# - after
#   - clone(Factory:ToTest)
#     - stripped
#     - after
#       - clone(Factory)...skip
#       - write real
#   - write real
def project_clone(apiurl_source, apiurl_target, project):
    users_clone(apiurl_source, apiurl_target, project)
    project_workaround(project)

    # Write stripped version that does not include repos with path references.
    url = makeurl(apiurl_target, ['source', project.get('name'), '_meta'])
    stripped = deepcopy(project)
    project_references_remove(stripped)
    http_PUT(url, data=ET.tostring(stripped))

    for link in project.xpath('link[@project]'):
        if not project_fence(link.get('project')):
            project.remove(link)
            break

        # Valid reference to project and thus should be cloned.
        path = ['source', link.get('project'), '_meta']
        entity_clone(apiurl_source, apiurl_target, path, clone=project_clone)

    # Clone projects referenced in repository paths.
    for repository in project.findall('repository'):
        for target in repository.xpath('./path') + repository.xpath('./releasetarget'):
            if not project_fence(target.get('project')):
                project.remove(repository)
                break

            # Valid reference to project and thus should be cloned.
            path = ['source', target.get('project'), '_meta']
            entity_clone(apiurl_source, apiurl_target, path, clone=project_clone)

def project_workaround(project):
    if project.get('name') == 'openSUSE:Factory':
        # See #1335 for details about temporary workaround in revision 429, but
        # suffice it to say that over-complicates clone with multiple loops and
        # may be introduced from time to time when Factory repo is hosed.
        scariness = project.xpath('repository[@name="standard"]/path[contains(@project, ":0-Bootstrap")]')
        if len(scariness):
            scariness[0].getparent().remove(scariness[0])

def package_clone(apiurl_source, apiurl_target, package):
    # Clone project that contains the package.
    path = ['source', package.get('project'), '_meta']
    entity_clone(apiurl_source, apiurl_target, path, clone=project_clone)

    # Clone the dependencies of package.
    users_clone(apiurl_source, apiurl_target, package)

    # Clone devel project referenced by package.
    devel = package.find('devel')
    if devel is not None:
        path = ['source', devel.get('project'), devel.get('package'), '_meta']
        entity_clone(apiurl_source, apiurl_target, path, clone=package_clone, after=package_clone_after)

def package_clone_after(apiurl_source, apiurl_target, package):
    copy_package(apiurl_source, package.get('project'), package.get('name'),
                 apiurl_target, package.get('project'), package.get('name'),
                 # TODO Would be preferable to preserve links, but need to
                 # recreat them since they do not match with copied package.
                 expand=True,
                 # TODO Can copy server-side if inner-connect is setup, but not
                 # clear how to trigger the equivalent of save in admin UI.
                 client_side_copy=True)

def person_sanitize(person):
    person.find('email').text = person.find('email').text.split('@')[0] + '@example.com'

def person_clone_after(apiurl_source, apiurl_target, person):
    url = makeurl(apiurl_target, ['person', person.find('login').text], {'cmd': 'change_password'})
    http_POST(url, data='opensuse')

def group_clone(apiurl_source, apiurl_target, group):
    for person in group.findall('maintainer') + group.findall('person/person'):
        path = ['person', person.get('userid')]
        entity_clone(apiurl_source, apiurl_target, path, person_sanitize, after=person_clone_after)

def clone_do(apiurl_source, apiurl_target, project):
    print('clone {} from {} to {}'.format(project, apiurl_source, apiurl_target))

    try:
        # TODO Decide how to choose what to clone via args.

        # Rather than handle the self-referencing craziness with a proper solver
        # the leaf can simple be used to start the chain and works as desired.
        # Disable this when running clone repeatedly during developing as the
        # projects cannot be cleanly re-created without more work.
        entity_clone(apiurl_source, apiurl_target, ['source', project + ':Rings:1-MinimalX', '_meta'],
                     clone=project_clone)

        pseudometa_project, pseudometa_package = project_pseudometa_package(apiurl_source, project)
        entity_clone(apiurl_source, apiurl_target, ['source', pseudometa_project, pseudometa_package, '_meta'],
                     clone=package_clone, after=package_clone_after)

        entity_clone(apiurl_source, apiurl_target, ['source', project, 'drush', '_meta'],
                     clone=package_clone, after=package_clone_after)

        entity_clone(apiurl_source, apiurl_target, ['group', 'opensuse-review-team'],
                     clone=group_clone)
    except HTTPError as e:
        # Print full output for any errors since message can be cryptic.
        print(e.read())
        return 1

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Clone projects and dependencies between OBS instances.')
    parser.set_defaults(func=clone_do)

    parser.add_argument('-S', '--apiurl-source', metavar='URL', help='source API URL')
    parser.add_argument('-T', '--apiurl-target', metavar='URL', help='target API URL')
    parser.add_argument('-c', '--cache', action='store_true', help='cache source queries for 24 hours')
    parser.add_argument('-d', '--debug', action='store_true', help='print info useful for debuging')
    parser.add_argument('-p', '--project', default='openSUSE:Factory', help='project from which to clone')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl_target)
    apiurl_target = osc.conf.config['apiurl']
    osc.conf.get_config(override_apiurl=args.apiurl_source)
    apiurl_source = osc.conf.config['apiurl']

    if apiurl_target == apiurl_source:
        print('target APIURL must not be the same as source APIURL')
        sys.exit(1)

    if args.cache:
        from osclib.cache import Cache
        Cache.PATTERNS = {}
        # Prevent caching source information from local clone.
        Cache.PATTERNS['/source/[^/]+/[^/]+/[^/]+?rev'] = 0
        Cache.PATTERNS['.*'] = Cache.TTL_LONG * 2
        Cache.init('clone')

    osc.conf.config['debug'] = args.debug
    project_fence.project = args.project
    sys.exit(args.func(apiurl_source, apiurl_target, args.project))
