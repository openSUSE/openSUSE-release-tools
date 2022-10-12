from collections import namedtuple
from datetime import datetime, timezone
from dateutil.parser import parse as date_parse
import re
import socket
import logging
from lxml import etree as ET
from urllib.error import HTTPError
from typing import Optional

from osc.core import create_submit_request
from osc.core import get_binarylist
from osc.core import get_commitlog
from osc.core import get_dependson
from osc.core import get_request_list
from osc.core import http_DELETE
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT
from osc.core import makeurl
from osc.core import owner
from osc.core import Request
from osc.core import Action
from osc.core import show_package_meta
from osc.core import show_project_meta
from osc.core import show_results_meta
from osc.core import xpath_join
from osc.util.helper import decode_it
from osc import conf
from osclib.conf import Config
from osclib.memoize import memoize
import traceback

BINARY_REGEX = r'(?:.*::)?(?P<filename>(?P<name>.*)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^-\.]+))'
RPM_REGEX = BINARY_REGEX + r'\.rpm'
BinaryParsed = namedtuple('BinaryParsed', ('package', 'filename', 'name', 'arch'))
REQUEST_STATES_MINUS_ACCEPTED = ['new', 'review', 'declined', 'revoked', 'superseded']


@memoize(session=True)
def group_members(apiurl, group, maintainers=False):
    url = makeurl(apiurl, ['group', group])
    root = ET.parse(http_GET(url)).getroot()

    if maintainers:
        return root.xpath('maintainer/@userid')

    return root.xpath('person/person/@userid')


def groups_members(apiurl, groups):
    members = []

    for group in groups:
        members.extend(group_members(apiurl, group))

    return members

# osc uses xml.etree while we rely on lxml


def convert_from_osc_et(xml):
    from xml.etree import ElementTree as oscET
    return ET.fromstring(oscET.tostring(xml))


@memoize(session=True)
def owner_fallback(apiurl, project, package):
    root = owner(apiurl, package, project=project)
    entry = root.find('owner')
    if not entry or project.startswith(entry.get('project')):
        # Fallback to global (ex Factory) maintainer.
        root = owner(apiurl, package)
    return convert_from_osc_et(root)


@memoize(session=True)
def maintainers_get(apiurl, project, package=None):
    if package is None:
        meta = ET.fromstringlist(show_project_meta(apiurl, project))
        maintainers = meta.xpath('//person[@role="maintainer"]/@userid')

        groups = meta.xpath('//group[@role="maintainer"]/@groupid')
        maintainers.extend(groups_members(apiurl, groups))

        return maintainers

    root = owner_fallback(apiurl, project, package)
    maintainers = root.xpath('//person[@role="maintainer"]/@name')

    groups = root.xpath('//group[@role="maintainer"]/@name')
    maintainers.extend(groups_members(apiurl, groups))

    return maintainers


@memoize(session=True)
def package_role_expand(apiurl, project, package, role='maintainer', inherit=True):
    """
    All users with a certain role on a package, including those who have the role directly assigned
    and those who are part of a group with that role.
    """
    meta = ET.fromstringlist(show_package_meta(apiurl, project, package))
    users = meta_role_expand(apiurl, meta, role)

    if inherit:
        users.extend(project_role_expand(apiurl, project, role))

    return users


@memoize(session=True)
def project_role_expand(apiurl, project, role='maintainer'):
    """
    All users with a certain role on a project, including those who have the role directly assigned
    and those who are part of a group with that role.
    """
    meta = ET.fromstringlist(show_project_meta(apiurl, project))
    return meta_role_expand(apiurl, meta, role)


def meta_role_expand(apiurl, meta, role='maintainer'):
    users = meta.xpath('//person[@role="{}"]/@userid'.format(role))

    groups = meta.xpath('//group[@role="{}"]/@groupid'.format(role))
    users.extend(groups_members(apiurl, groups))

    return users


def package_list(apiurl, project, expand=True):
    query = {}
    if expand:
        query['expand'] = 1
    url = makeurl(apiurl, ['source', project], query)
    root = ET.parse(http_GET(url)).getroot()

    packages = []
    for package in root.findall('entry'):
        packages.append(package.get('name'))

    return sorted(packages)


@memoize(session=True)
def target_archs(apiurl, project, repository='standard'):
    meta = ET.fromstringlist(show_project_meta(apiurl, project))
    return meta.xpath('repository[@name="{}"]/arch/text()'.format(repository))


@memoize(session=True)
def depends_on(apiurl, project, repository, packages=None, reverse=None):
    dependencies = set()
    for arch in target_archs(apiurl, project, repository):
        root = ET.fromstring(get_dependson(apiurl, project, repository, arch, packages, reverse))
        dependencies.update(pkgdep.text for pkgdep in root.findall('.//pkgdep'))

    return dependencies


def request_when_staged(request, project, first=False):
    when = None
    for history in request.statehistory:
        if project in history.comment:
            when = history.when

    return date_parse(when)


def binary_list(apiurl, project, repository, arch, package=None):
    parsed = []
    for binary in get_binarylist(apiurl, project, repository, arch, package):
        result = re.match(RPM_REGEX, binary)
        if not result:
            continue

        name = result.group('name')
        if name.endswith('-debuginfo') or name.endswith('-debuginfo-32bit'):
            continue
        if name.endswith('-debugsource'):
            continue
        if result.group('arch') == 'src':
            continue

        parsed.append(BinaryParsed(package, result.group('filename'), name, result.group('arch')))

    return parsed


@memoize(session=True)
def package_binary_list(apiurl, project, repository, arch, package=None, strip_multibuild=True, exclude_src_debug=False):
    path = ['build', project, repository, arch]
    if package:
        path.append(package)
    url = makeurl(apiurl, path, {'view': 'binaryversions'})
    root = ET.parse(http_GET(url)).getroot()

    package_binaries = []
    binary_map = {}  # last duplicate wins
    for binary_list in root:
        package = binary_list.get('package')
        if strip_multibuild:
            package = package.split(':', 1)[0]

        for binary in binary_list:
            filename = binary.get('name')
            result = re.match(RPM_REGEX, filename)
            if not result:
                continue

            binary = BinaryParsed(package, result.group('filename'),
                                  result.group('name'), result.group('arch'))
            if exclude_src_debug and binary_src_debug(binary):
                continue

            package_binaries.append(binary)
            binary_map[result.group('filename')] = package

    return package_binaries, binary_map


def binary_src_debug(binary):
    return (
        binary.arch == 'src' or
        binary.arch == 'nosrc' or
        binary.name.endswith('-debuginfo') or
        binary.name.endswith('-debugsource')
    )


@memoize(session=True)
def devel_project_get(apiurl, target_project, target_package):
    try:
        meta = ET.fromstringlist(show_package_meta(apiurl, target_project, target_package))
        node = meta.find('devel')
        if node is not None:
            return node.get('project'), node.get('package')
    except HTTPError as e:
        if e.code != 404:
            raise e

    return None, None


@memoize(session=True)
def devel_project_fallback(apiurl, target_project, target_package):
    project, package = devel_project_get(apiurl, target_project, target_package)
    if project is None and target_project != 'openSUSE:Factory':
        if target_project.startswith('openSUSE:'):
            project, package = devel_project_get(apiurl, 'openSUSE:Factory', target_package)
        elif target_project.startswith('SUSE:'):
            # For SLE (assume IBS), fallback to openSUSE:Factory devel projects.
            project, package = devel_project_get(apiurl, 'openSUSE.org:openSUSE:Factory', target_package)
            if project:
                # Strip openSUSE.org: prefix since string since not used for lookup.
                project = project.split(':', 1)[1]

    return project, package


@memoize(session=True)
def devel_projects(apiurl, project):
    devel_projects = set()

    root = search(apiurl, 'package', "@project='{}' and devel/@project!=''".format(project))
    for devel_project in root.xpath('package/devel/@project'):
        if devel_project != project:
            devel_projects.add(devel_project)

    return sorted(devel_projects)


def request_created(request):
    if isinstance(request, Request):
        created = request.statehistory[0].when
    else:
        created = request.find('history').get('when')
    return date_parse(created)


def request_age(request):
    return datetime.utcnow() - request_created(request)


def project_list_prefix(apiurl, prefix):
    """Get a list of project with the same prefix."""
    query = {'match': 'starts-with(@name, "{}")'.format(prefix)}
    url = makeurl(apiurl, ['search', 'project', 'id'], query)
    root = ET.parse(http_GET(url)).getroot()
    return root.xpath('project/@name')


def project_locked(apiurl, project):
    meta = ET.fromstringlist(show_project_meta(apiurl, project))
    return meta.find('lock/enable') is not None

#
# Depdendency helpers
#


def fileinfo_ext_all(apiurl, project, repo, arch, package):
    url = makeurl(apiurl, ['build', project, repo, arch, package])
    binaries = ET.parse(http_GET(url)).getroot()
    for binary in binaries.findall('binary'):
        filename = binary.get('filename')
        if not filename.endswith('.rpm'):
            continue

        yield fileinfo_ext(apiurl, project, repo, arch, package, filename)


def fileinfo_ext(apiurl, project, repo, arch, package, filename):
    url = makeurl(apiurl,
                  ['build', project, repo, arch, package, filename],
                  {'view': 'fileinfo_ext'})
    return ET.parse(http_GET(url)).getroot()


def builddepinfo(apiurl, project, repo, arch, order=False):
    query = {}
    if order:
        query['view'] = 'order'
    url = makeurl(apiurl, ['build', project, repo, arch, '_builddepinfo'], query)
    return ET.parse(http_GET(url)).getroot()


def entity_email(apiurl, key, entity_type='person', include_name=False):
    url = makeurl(apiurl, [entity_type, key])
    root = ET.parse(http_GET(url)).getroot()

    email = root.find('email')
    if email is None:
        return None
    email = email.text

    realname = root.find('realname')
    if include_name and realname is not None:
        email = '{} <{}>'.format(realname.text, email)

    return email


def source_file_load(apiurl, project, package, filename, revision=None):
    query = {'expand': 1}
    if revision:
        query['rev'] = revision
    url = makeurl(apiurl, ['source', project, package, filename], query)
    try:
        return decode_it(http_GET(url).read())
    except HTTPError:
        return None


def source_file_save(apiurl, project, package, filename, content, comment=None):
    comment = message_suffix('updated', comment)
    url = makeurl(apiurl, ['source', project, package, filename], {'comment': comment})
    http_PUT(url, data=content)


def source_file_ensure(apiurl, project, package, filename, content, comment=None):
    if content != source_file_load(apiurl, project, package, filename):
        source_file_save(apiurl, project, package, filename, content, comment)


def project_pseudometa_package(apiurl, project):
    package = Config.get(apiurl, project).get('pseudometa_package', '00Meta')
    if '/' in package:
        project, package = package.split('/', 2)

    return project, package


def project_pseudometa_file_load(apiurl, project, filename, revision=None):
    project, package = project_pseudometa_package(apiurl, project)
    source_file = source_file_load(apiurl, project, package, filename, revision)
    if source_file is not None:
        source_file = source_file.rstrip()
    return source_file


def project_pseudometa_file_save(apiurl, project, filename, content, comment=None):
    project, package = project_pseudometa_package(apiurl, project)
    source_file_save(apiurl, project, package, filename, content, comment)


def project_pseudometa_file_ensure(apiurl, project, filename, content, comment=None):
    if content != project_pseudometa_file_load(apiurl, project, filename):
        project_pseudometa_file_save(apiurl, project, filename, content, comment)

# Should be an API call that says give me "real" packages that does not include
# multibuild entries, nor linked packages, nor maintenance update packages, but
# does included inherited packages from project layering. Unfortunately, no such
# call provides either server-side filtering nor enough information to filter
# client-side. As such extra calls must be made for each package to handle the
# various different cases that can exist between products. For a more detailed
# write-up see the opensuse-buildservice mailing list thread:
# https://lists.opensuse.org/opensuse-buildservice/2019-05/msg00020.html.


def package_list_kind_filtered(apiurl, project, kinds_allowed=['source']):
    query = {
        'view': 'info',
        'nofilename': '1',
    }
    url = makeurl(apiurl, ['source', project], query)
    root = ET.parse(http_GET(url)).getroot()

    for package in root.xpath('sourceinfo/@package'):
        kind = package_kind(apiurl, project, package)
        if kind not in kinds_allowed:
            continue

        yield package


def attribute_value_load(apiurl: str, project: str, name: str, namespace='OSRT', package: Optional[str] = None):
    path = list(filter(None, ['source', project, package, '_attribute', namespace + ':' + name]))
    url = makeurl(apiurl, path)

    try:
        root = ET.parse(http_GET(url)).getroot()
    except HTTPError as e:
        if e.code == 404:
            return None

        raise e

    xpath_base = './attribute[@namespace="{}" and @name="{}"]'.format(namespace, name)
    value = root.xpath('{}/value/text()'.format(xpath_base))
    if not len(value):
        if root.xpath(xpath_base):
            # Handle boolean attributes that are present, but have no value.
            return True
        return None

    return str(value[0])

# New attributes must be defined manually before they can be used. Example:
#   `osc api /attribute/OSRT/IgnoredIssues/_meta outputs`
#
# The new attribute can be created via:
#   `api -T $xml /attribute/OSRT/$NEWATTRIBUTE/_meta`
#
# Remember to create for both OBS and IBS as necessary.


def attribute_value_save(
    apiurl: str,
    project: str,
    name: str,
    value: str,
    namespace='OSRT',
    package: Optional[str] = None
):
    root = ET.Element('attributes')

    attribute = ET.SubElement(root, 'attribute')
    attribute.set('namespace', namespace)
    attribute.set('name', name)

    ET.SubElement(attribute, 'value').text = value

    # The OBS API of attributes is super strange, POST to update.
    url = makeurl(apiurl, list(filter(None, ['source', project, package, '_attribute'])))
    try:
        http_POST(url, data=ET.tostring(root))
    except HTTPError as e:
        if e.code == 404:
            logging.error(f"Saving attribute {namespace}:{name} to {project} failed. You may need to create the type on your instance.")
        raise e


def attribute_value_delete(apiurl: str, project: str, name: str, namespace='OSRT', package: Optional[str] = None):
    http_DELETE(makeurl(
        apiurl, list(filter(None, ['source', project, package, '_attribute', namespace + ':' + name]))))


@memoize(session=True)
def repository_path_expand(apiurl: str, project: str, repo: str, visited_repos: Optional[set] = None):
    """Recursively list underlying projects."""
    if visited_repos is None:
        visited_repos = set()
    repos = [[project, repo]]
    meta = ET.fromstringlist(show_project_meta(apiurl, project))
    paths = meta.findall('.//repository[@name="{}"]/path'.format(repo))

    # The listed paths are taken as-is, except for the last one...
    for path in paths[:-1]:
        repos += [[path.get('project', project), path.get('repository')]]

    # ...which is expanded recursively
    if len(paths) > 0:
        p_project = paths[-1].get('project', project)
        p_repository = paths[-1].get('repository')
        if (p_project, p_repository) not in visited_repos:
            visited_repos.add((p_project, p_repository))
            repos += repository_path_expand(apiurl, p_project, p_repository, visited_repos)
    return repos


@memoize(session=True)
def repository_path_search(apiurl, project, search_project, search_repository):
    queue = []

    # Initialize breadth first search queue with repositories from top project.
    root = ET.fromstringlist(show_project_meta(apiurl, project))
    for repository in root.xpath('repository[path[@project and @repository]]/@name'):
        queue.append((repository, project, repository))

    # Perform a breadth first search and return the first repository chain with
    # a series of path elements targeting search project and repository.
    for repository_top, project, repository in queue:
        if root.get('name') != project:
            # Repositories for a single project are in a row so cache parsing.
            root = ET.fromstringlist(show_project_meta(apiurl, project))

        paths = root.findall('repository[@name="{}"]/path'.format(repository))
        for path in paths:
            if path.get('project') == search_project and path.get('repository') == search_repository:
                return repository_top

            queue.append((repository_top, path.get('project'), path.get('repository')))

    return None


def repository_arch_state(apiurl, project, repository, arch):
    # just checking the mtimes of the repository's binaries
    url = makeurl(apiurl, ['build', project, repository, arch, '_repository'])
    from osclib.util import sha1_short
    try:
        return sha1_short(http_GET(url).read())
    except HTTPError as e:
        # e.g. staging projects inherit the project config from 'ports' repository.
        # but that repository does not contain the archs we want, as such it has no state
        if e.code != 404:
            raise e


def repository_state(apiurl, project, repository, archs=[]):
    if not len(archs):
        archs = target_archs(apiurl, project, repository)

    # Unfortunately, the state hash reflects the published state and not the
    # binaries published in repository. As such request binary list and hash.
    combined_state = []
    for arch in archs:
        state = repository_arch_state(apiurl, project, repository, arch)
        if state:
            combined_state.append(state)
    from osclib.util import sha1_short
    return sha1_short(combined_state)


def repositories_states(apiurl, repository_pairs, archs=[]):
    states = []

    for project, repository in repository_pairs:
        state = repository_state(apiurl, project, repository, archs)
        if state:
            states.append(state)

    return states


def repository_published(apiurl, project, repository, archs=[]):
    # In a perfect world this would check for the existence of imports from i586
    # into x86_64, but in an even more perfect world OBS would show archs that
    # depend on another arch for imports as not completed until the dependent
    # arch completes. This is a simplified check that ensures x86_64 repos are
    # not indicated as published when i586 has not finished which is primarily
    # useful for repo_checker when only checking x86_64. The API treats archs as
    # a filter on what to return and thus non-existent archs do not cause an
    # issue nor alter the result.
    if 'x86_64' in archs and 'i586' not in archs:
        # Create a copy to avoid altering caller's list.
        archs = list(archs)
        archs.append('i586')

    root = ET.fromstringlist(show_results_meta(
        apiurl, project, multibuild=True, repository=[repository], arch=archs))
    return not len(root.xpath('result[@state!="published" and @state!="unpublished"]'))


def repositories_published(apiurl, repository_pairs, archs=[]):
    for project, repository in repository_pairs:
        if not repository_published(apiurl, project, repository, archs):
            return (project, repository)

    return True


def project_meta_revision(apiurl, project):
    root = ET.fromstringlist(get_commitlog(
        apiurl, project, '_project', None, format='xml', meta=True))
    return int(root.find('logentry').get('revision'))


def package_source_changed(apiurl, project, package):
    url = makeurl(apiurl, ['source', project, package, '_history'], {'limit': 1})
    root = ET.parse(http_GET(url)).getroot()
    return datetime.fromtimestamp(int(root.find('revision/time').text), timezone.utc).replace(tzinfo=None)


def package_source_age(apiurl, project, package):
    return datetime.utcnow() - package_source_changed(apiurl, project, package)


def entity_exists(apiurl, project, package=None):
    try:
        http_GET(makeurl(apiurl, list(filter(None, ['source', project, package])) + ['_meta']))
    except HTTPError as e:
        if e.code == 404:
            return False

        raise e

    return True


def package_kind(apiurl, project, package):
    if package.startswith('00') or package.startswith('_'):
        return 'meta'

    if ':' in package:
        return 'multibuild_subpackage'

    if package.startswith('patchinfo.'):
        return 'patchinfo'

    try:
        url = makeurl(apiurl, ['source', project, package, '_meta'])
        root = ET.parse(http_GET(url)).getroot()
    except HTTPError as e:
        if e.code == 404:
            return None

        raise e

    if root.find('releasename') is not None and root.find('releasename').text != package:
        return 'maintenance_update'

    # Some multispec subpackages do not have bcntsynctag, so check link.
    link = entity_source_link(apiurl, project, package)
    if link is not None and link.get('cicount') == 'copy':
        kind_target = package_kind(apiurl, project, link.get('package'))
        if kind_target != 'maintenance_update':
            # If a multispec subpackage was updated via a maintenance update the
            # proper link information is lost and it will be considered source.
            return 'multispec_subpackage'

    return 'source'


def entity_source_link(apiurl, project, package=None):
    try:
        if package:
            parts = ['source', project, package, '_link']
        else:
            parts = ['source', project, '_meta']
        url = makeurl(apiurl, parts)
        root = ET.parse(http_GET(url)).getroot()
    except HTTPError as e:
        if e.code == 404:
            return None

        raise e

    return root if package else root.find('link')


@memoize(session=True)
def package_source_link_copy(apiurl, project, package):
    link = entity_source_link(apiurl, project, package)
    return link is not None and link.get('cicount') == 'copy'

# Ideally, all package_source_hash* functions would operate on srcmd5, but
# unfortunately that is not practical for real use-cases. The srcmd5 includes
# service run information in addition to the presence of a link even if the
# expanded sources are identical. The verifymd5 sum excludes such information
# and only covers the sources (as should be the point), but looks at the link
# sources which means for projects like devel which link to the head revision of
# downstream all the verifymd5 sums are the same. This makes the summary md5s
# provided by OBS useless for comparing source and really anything. Instead the
# individual file md5s are used to generate a sha1 which is used for comparison.
# In the case of maintenance projects they are structured such that the updates
# are suffixed packages and the unsuffixed package is empty and only links to
# a specific suffixed package each revision. As such for maintenance projects
# the link must be expanded and is safe to do so. Additionally, projects that
# inherit packages need to same treatment (ie. expanding) until they are
# overridden within the project.


@memoize(session=True)
def package_source_hash(apiurl, project, package, revision=None):
    query = {}
    if revision:
        query['rev'] = revision

    # Will not catch packages that previous had a link, but no longer do.
    if package_source_link_copy(apiurl, project, package):
        query['expand'] = 1

    try:
        url = makeurl(apiurl, ['source', project, package], query)
        root = ET.parse(http_GET(url)).getroot()
    except HTTPError as e:
        if e.code == 400 or e.code == 404:
            # 400: revision not found, 404: package not found.
            return None

        raise e

    if revision and root.find('error') is not None:
        # OBS returns XML error instead of HTTP 404 if revision not found.
        return None

    from osclib.util import sha1_short
    return sha1_short(root.xpath('entry[@name!="_link"]/@md5'))


def package_source_hash_history(apiurl, project, package, limit=5, include_project_link=False):
    try:
        # get_commitlog() reverses the order so newest revisions are first.
        root = ET.fromstringlist(
            get_commitlog(apiurl, project, package, None, format='xml'))
    except HTTPError as e:
        if e.code == 404:
            return

        raise e

    if include_project_link:
        source_hashes = []

    source_md5s = root.xpath('logentry/@srcmd5')
    for source_md5 in source_md5s[:limit]:
        source_hash = package_source_hash(apiurl, project, package, source_md5)
        yield source_hash

        if include_project_link:
            source_hashes.append(source_hash)

    if include_project_link and (not limit or len(source_md5s) < limit):
        link = entity_source_link(apiurl, project)
        if link is None:
            return
        project = link.get('project')

        if limit:
            limit_remaining = limit - len(source_md5s)

        # Allow small margin for duplicates.
        for source_hash in package_source_hash_history(apiurl, project, package, None, True):
            if source_hash in source_hashes:
                continue

            yield source_hash

            if limit:
                limit_remaining += -1
                if limit_remaining == 0:
                    break


def package_version(apiurl, project, package):
    try:
        url = makeurl(apiurl, ['source', project, package, '_history'], {'limit': 1})
        root = ET.parse(http_GET(url)).getroot()
    except HTTPError as e:
        if e.code == 404:
            return False

        raise e

    return str(root.xpath('(//version)[last()]/text()')[0])


def project_attribute_list(apiurl, attribute, locked=None):
    xpath = 'attribute/@name="{}"'.format(attribute)
    root = search(apiurl, 'project', xpath)
    for project in root.xpath('project/@name'):
        # Locked not exposed via OBS xpath engine.
        if locked is not None and project_locked(apiurl, project) != locked:
            continue

        yield project

# OBS xpath engine does not support multiple attribute queries nor negation. As
# such both must be done client-side.


def project_attributes_list(apiurl, attributes, attributes_not=None, locked=None):
    projects = set()

    for attribute in attributes:
        projects.update(project_attribute_list(apiurl, attribute, locked))

    for attribute in attributes_not:
        projects.difference_update(project_attribute_list(apiurl, attribute, locked))

    return list(projects)


@memoize(session=True)
def project_remote_list(apiurl):
    remotes = {}

    root = search(apiurl, 'project', 'starts-with(remoteurl, "http")')
    for project in root.findall('project'):
        # Strip ending /public as the only use-cases for manually checking
        # remote projects is to query them directly to use an API that does not
        # work over the interconnect. As such /public will have same problem.
        remotes[project.get('name')] = re.sub('/public$', '', project.find('remoteurl').text)

    return remotes


def project_remote_apiurl(apiurl, project):
    remotes = project_remote_list(apiurl)
    for remote in remotes:
        if project.startswith(remote + ':'):
            return remotes[remote], project[len(remote) + 1:]

    return apiurl, project


def project_remote_prefixed(apiurl, apiurl_remote, project):
    if apiurl_remote == apiurl:
        return project

    remotes = project_remote_list(apiurl)
    for remote, remote_apiurl in remotes.items():
        if remote_apiurl == apiurl_remote:
            return remote + ':' + project

    raise Exception('remote APIURL interconnect not configured for{}'.format(apiurl_remote))


def review_find_last(request, user, states=['all']):
    for review in reversed(request.reviews):
        if review.by_user == user and ('all' in states or review.state in states):
            return review

    return None


def reviews_remaining(request, incident_psuedo=False):
    reviews = []
    for review in request.reviews:
        if review.state != 'accepted':
            reviews.append(review_short(review))

    if incident_psuedo:
        # Add review in the same style as the staging review used for non
        # maintenance projects to allow for the same wait on review.
        for action in request.actions:
            if action.type == 'maintenance_incident':
                reviews.append('maintenance_incident')
                break

    return reviews


def review_short(review):
    if review.by_user:
        return review.by_user
    if review.by_group:
        return review.by_group
    if review.by_project:
        if review.by_package:
            return '/'.join([review.by_project, review.by_package])
        return review.by_project

    return None


def issue_trackers(apiurl):
    url = makeurl(apiurl, ['issue_trackers'])
    root = ET.parse(http_GET(url)).getroot()
    trackers = {}
    for tracker in root.findall('issue-tracker'):
        trackers[tracker.find('name').text] = tracker.find('label').text
    return trackers


def issue_tracker_by_url(apiurl, tracker_url):
    url = makeurl(apiurl, ['issue_trackers'])
    root = ET.parse(http_GET(url)).getroot()
    if not tracker_url.endswith('/'):
        # All trackers are formatted with trailing slash.
        tracker_url += '/'
    return next(iter(root.xpath('issue-tracker[url[text()="{}"]]'.format(tracker_url)) or []), None)


def issue_tracker_label_apply(tracker, identifier):
    return tracker.find('label').text.replace('@@@', identifier)


def request_remote_identifier(apiurl, apiurl_remote, request_id):
    if apiurl_remote == apiurl:
        return 'request#{}'.format(request_id)

    # The URL differences make this rather convoluted.
    tracker = issue_tracker_by_url(apiurl, apiurl_remote.replace('api.', 'build.'))
    if tracker is not None:
        return issue_tracker_label_apply(tracker, request_id)

    return request_id


def duplicated_binaries_in_repo(apiurl, project, repository):
    duplicates = {}
    for arch in sorted(target_archs(apiurl, project, repository), reverse=True):
        package_binaries, _ = package_binary_list(
            apiurl, project, repository, arch,
            strip_multibuild=False, exclude_src_debug=True)
        binaries = {}
        for pb in package_binaries:
            if pb.arch != 'noarch' and pb.arch != arch:
                continue

            binaries.setdefault(arch, {})

            if pb.name in binaries[arch]:
                duplicates.setdefault(str(arch), {})
                duplicates[arch].setdefault(pb.name, set())
                duplicates[arch][pb.name].add(pb.package)
                duplicates[arch][pb.name].add(binaries[arch][pb.name])
                continue

            binaries[arch][pb.name] = pb.package

    # convert sets to lists for readable yaml
    for arch in duplicates.keys():
        for name in duplicates[arch].keys():
            duplicates[arch][name] = list(duplicates[arch][name])

    return duplicates

# osc.core.search() is over-complicated and does not return lxml element.


def search(apiurl, path, xpath, query={}):
    query['match'] = xpath
    url = makeurl(apiurl, ['search', path], query)
    return ET.parse(http_GET(url)).getroot()


def action_is_patchinfo(action):
    return (action.type == 'maintenance_incident' and (
        action.src_package == 'patchinfo' or action.src_package.startswith('patchinfo.')))


def request_action_key(action):
    identifier = []

    if action.type in ['add_role', 'change_devel', 'maintenance_release', 'set_bugowner', 'submit']:
        identifier.append(action.tgt_project)
        if action.tgt_package is not None:
            identifier.append(action.tgt_package)

        if action.type in ['add_role', 'set_bugowner']:
            if action.person_name is not None:
                identifier.append(action.person_name)
                if action.type == 'add_role':
                    identifier.append(action.person_role)
            else:
                identifier.append(action.group_name)
                if action.type == 'add_role':
                    identifier.append(action.group_role)
    elif action.type == 'delete':
        identifier.append(action.tgt_project)
        if action.tgt_package is not None:
            identifier.append(action.tgt_package)
        elif action.tgt_repository is not None:
            identifier.append(action.tgt_repository)
    elif action.type == 'maintenance_incident':
        if not action_is_patchinfo(action):
            identifier.append(action.tgt_releaseproject)
        identifier.append(action.src_package)

    return '::'.join(['/'.join(identifier), action.type])


def request_action_list_maintenance_incident(apiurl, project, package, states=['new', 'review']):
    # The maintenance workflow seems to be designed to be as difficult to find
    # requests as possible. As such, in order to find incidents for a given
    # target project one must search for the requests in two states: before and
    # after being assigned to an incident project. Additionally, one must search
    # the "maintenance projects" denoted by an attribute instead of the actual
    # target project. To make matters worse the actual target project of the
    # request is not accessible via search (ie. action/target/releaseproject)
    # so it must be checked client side. Lastly, since multiple actions are also
    # designed completely wrong one must loop over the actions and recheck the
    # search parameters to figure out which action caused the request to be
    # included in the search results. Overall, another prime example of design
    # done completely and utterly wrong.

    package_repository = '{}.{}'.format(package, project.replace(':', '_'))

    # Loop over all maintenance projects and create selectors for the two
    # request states for the given project.
    xpath = ''
    for maintenance_project in project_attribute_list(apiurl, 'OBS:MaintenanceProject'):
        xpath_project = ''

        # Before being assigned to an incident.
        xpath_project = xpath_join(xpath_project, 'action/target/@project="{}"'.format(
            maintenance_project))

        xpath_project_package = ''
        xpath_project_package = xpath_join(
            xpath_project_package, 'action/source/@package="{}"'.format(package))
        xpath_project_package = xpath_join(
            xpath_project_package, 'action/source/@package="{}"'.format(
                package_repository), op='or', inner=True)

        xpath_project = xpath_join(xpath_project, f'({xpath_project_package})', op='and', inner=True)

        xpath = xpath_join(xpath, xpath_project, op='or', nexpr_parentheses=True)
        xpath_project = ''

        # After being assigned to an incident.
        xpath_project = xpath_join(xpath_project, 'starts-with(action/target/@project,"{}:")'.format(
            maintenance_project))
        xpath_project = xpath_join(xpath_project, 'action/target/@package="{}"'.format(
            package_repository), op='and', inner=True)

        xpath = xpath_join(xpath, xpath_project, op='or', nexpr_parentheses=True)

    xpath = '({})'.format(xpath)

    if 'all' not in states:
        xpath_states = ''
        for state in states:
            xpath_states = xpath_join(xpath_states, 'state/@name="{}"'.format(state), inner=True)
        xpath = xpath_join(xpath, xpath_states, op='and', nexpr_parentheses=True)

    xpath = xpath_join(xpath, 'action/@type="maintenance_incident"', op='and')

    root = search(apiurl, 'request', xpath)
    for request_element in root.findall('request'):
        request = Request()
        request.read(request_element)

        for action in request.actions:
            if action.type == 'maintenance_incident' and action.tgt_releaseproject == project and (
                (action.tgt_package is None and
                    (action.src_package == package or action.src_package == package_repository)) or
                    (action.tgt_package == package_repository)):
                yield request, action
                break


def request_action_list_maintenance_release(apiurl, project, package, states=['new', 'review']):
    package_repository = '{}.{}'.format(package, project.replace(':', '_'))

    xpath = 'action/target/@project="{}"'.format(project)
    xpath = xpath_join(xpath, 'action/source/@package="{}"'.format(package_repository), op='and', inner=True)
    xpath = '({})'.format(xpath)

    if 'all' not in states:
        xpath_states = ''
        for state in states:
            xpath_states = xpath_join(xpath_states, 'state/@name="{}"'.format(state), inner=True)
        xpath = xpath_join(xpath, xpath_states, op='and', nexpr_parentheses=True)

    xpath = xpath_join(xpath, 'action/@type="maintenance_release"', op='and')

    root = search(apiurl, 'request', xpath)
    for request_element in root.findall('request'):
        request = Request()
        request.read(request_element)

        for action in request.actions:
            if (action.type == 'maintenance_release' and
                    action.tgt_project == project and action.src_package == package_repository):
                yield request, action
                break


def request_action_simple_list(apiurl, project, package, states, request_type):
    # Disable including source project in get_request_list() query.
    before = conf.config['include_request_from_project']
    conf.config['include_request_from_project'] = False
    requests = get_request_list(apiurl, project, package, None, states, request_type, withfullhistory=True)
    conf.config['include_request_from_project'] = before

    for request in requests:
        for action in request.actions:
            if action.tgt_project == project and action.tgt_package == package:
                yield request, action
                break


def request_action_list(apiurl, project, package, states=['new', 'review'], types=['submit']):
    for request_type in types:
        if request_type == 'maintenance_incident':
            yield from request_action_list_maintenance_incident(apiurl, project, package, states)
        if request_type == 'maintenance_release':
            yield from request_action_list_maintenance_release(apiurl, project, package, states)
        else:
            yield from request_action_simple_list(apiurl, project, package, states, request_type)


def request_action_list_source(apiurl, project, package, states=['new', 'review'], include_release=False):
    types = []
    if attribute_value_load(apiurl, project, 'Maintained', 'OBS'):
        types.append('maintenance_incident')
        if include_release:
            types.append('maintenance_release')
    else:
        types.append('submit')

    yield from request_action_list(apiurl, project, package, states, types)


def request_create_submit(apiurl, source_project, source_package,
                          target_project, target_package=None, message=None, revision=None,
                          ignore_if_any_request=False, supersede=True, frequency=None):
    """
    ignore_if_any_request: ignore source changes and do not submit if any prior requests
    """

    if not target_package:
        target_package = source_package

    source_hash = package_source_hash(apiurl, target_project, target_package)
    source_hash_consider = package_source_hash(apiurl, source_project, source_package, revision)
    if source_hash_consider == source_hash:
        # No sense submitting identical sources.
        return False

    for request, action in request_action_list(
            apiurl, target_project, target_package, REQUEST_STATES_MINUS_ACCEPTED, ['submit']):
        if ignore_if_any_request:
            return False
        if not supersede and request.state.name in ('new', 'review'):
            return False
        if frequency and request_age(request).total_seconds() < frequency:
            return False

        source_hash_pending = package_source_hash(
            apiurl, action.src_project, action.src_package, action.src_rev)
        if source_hash_pending == source_hash_consider:
            # Pending request with identical sources.
            return False

    message = message_suffix('created', message)

    def create_function():
        return create_submit_request(apiurl, source_project, source_package,
                                     target_project, target_package,
                                     message=message, orev=revision)

    return RequestFuture('submit {}/{} -> {}/{}'.format(
        source_project, source_package, target_project, target_package), create_function)


def request_create_delete(apiurl, target_project, target_package, message=None):
    for request, action in request_action_list(
            apiurl, target_project, target_package, REQUEST_STATES_MINUS_ACCEPTED, ['delete']):
        return False

    # No proper API function to perform the same operation.
    message = message_suffix('created', message)

    def create_function():
        return create_delete_request(apiurl, target_project, target_package, message)

    return RequestFuture('delete {}/{}'.format(target_project, target_package), create_function)


def request_create_change_devel(apiurl, source_project, source_package,
                                target_project, target_package=None, message=None):
    if not target_package:
        target_package = source_package

    for request, action in request_action_list(
            apiurl, target_project, target_package, REQUEST_STATES_MINUS_ACCEPTED, ['change_devel']):
        return False

    message = message_suffix('created', message)

    def create_function():
        return create_change_devel_request(apiurl, source_project, source_package,
                                           target_project, target_package, message)

    return RequestFuture('change_devel {}/{} -> {}/{}'.format(
        source_project, source_package, target_project, target_package), create_function)


def create_delete_request(apiurl, target_project, target_package=None, message=None):
    """Create a delete request"""

    action = Action('delete', tgt_project=target_project, tgt_package=target_package)
    return create_request(apiurl, action, message)


def create_change_devel_request(apiurl, source_project, source_package,
                                target_project, target_package=None, message=None):
    """Create a change_devel request"""

    action = Action('change_devel', src_project=source_project, src_package=source_package,
                    tgt_project=target_project, tgt_package=target_package)
    return create_request(apiurl, action, message)


def create_add_role_request(apiurl, target_project, user, role, target_package=None, message=None):
    """Create an add_role request

    user -- user or group name. If it is a group, it has to start with 'group:'.
    """

    if user.startswith('group:'):
        group = user.replace('group:', '')
        kargs = dict(group_name=group, group_role=role)
    else:
        kargs = dict(person_name=user, person_role=role)

    action = Action('add_role', tgt_project=target_project, tgt_package=target_package, **kargs)
    return create_request(apiurl, action, message)


def create_set_bugowner_request(apiurl, target_project, user, target_package=None, message=None):
    """Create an set_bugowner request

    user -- user or group name. If it is a group, it has to start with 'group:'.
    """

    if user.startswith('group:'):
        group = user.replace('group:', '')
        kargs = dict(group_name=group)
    else:
        kargs = dict(person_name=user)

    action = Action('set_bugowner', tgt_project=target_project, tgt_package=target_package, **kargs)
    return create_request(apiurl, action, message)


def create_request(apiurl, action, message=None):
    """Create a request for the given action

    If no message is given, one is generated (see add_description)
    """

    r = Request()
    r.actions.append(action)
    add_description(r, message)

    r.create(apiurl)
    return r.reqid


class RequestFuture:
    def __init__(self, description, create_function):
        self.description = description
        self.create_function = create_function

    def create(self):
        return self.create_function()

    def create_tolerant(self):
        try:
            return self.create()
        except HTTPError:
            traceback.print_exc()

        return False

    def print_and_create(self, dry=False):
        if dry:
            print(self)
            return None

        request_id = self.create_tolerant()
        print('{} = {}'.format(self, request_id))
        return request_id

    def __str__(self):
        return self.description


def add_description(request, text=None):
    """Add a description to the given request.

    If a text is given, that is used as description. Otherwise a generic text is generated.
    """

    if not text:
        text = message_suffix('created')
    request.description = text


def message_suffix(action, message=None):
    if not message:
        message = '{} by OSRT tools'.format(action)

    message += ' (host {})'.format(socket.gethostname())
    return message


def request_state_change(apiurl, request_id, state):
    query = {'newstate': state, 'cmd': 'changestate'}
    url = makeurl(apiurl, ['request', request_id], query)
    return ET.parse(http_POST(url)).getroot().get('code')
