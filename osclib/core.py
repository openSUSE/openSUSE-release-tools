from collections import namedtuple
from datetime import datetime
from dateutil.parser import parse as date_parse
import re
import socket
from xml.etree import cElementTree as ET
from lxml import etree as ETL

try:
    from urllib.error import HTTPError
except ImportError:
    #python 2.x
    from urllib2 import HTTPError

from osc.core import get_binarylist
from osc.core import get_commitlog
from osc.core import get_dependson
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT
from osc.core import makeurl
from osc.core import owner
from osc.core import Request
from osc.core import show_package_meta
from osc.core import show_project_meta
from osc.core import show_results_meta
from osclib.conf import Config
from osclib.memoize import memoize

BINARY_REGEX = r'(?:.*::)?(?P<filename>(?P<name>.*)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^-\.]+))'
RPM_REGEX = BINARY_REGEX + r'\.rpm'
BinaryParsed = namedtuple('BinaryParsed', ('package', 'filename', 'name', 'arch'))

@memoize(session=True)
def group_members(apiurl, group, maintainers=False):
    url = makeurl(apiurl, ['group', group])
    root = ETL.parse(http_GET(url)).getroot()

    if maintainers:
        return root.xpath('maintainer/@userid')

    return root.xpath('person/person/@userid')

def groups_members(apiurl, groups):
    members = []

    for group in groups:
        members.extend(group_members(apiurl, group))

    return members

@memoize(session=True)
def owner_fallback(apiurl, project, package):
    root = owner(apiurl, package, project=project)
    entry = root.find('owner')
    if not entry or project.startswith(entry.get('project')):
        # Fallback to global (ex Factory) maintainer.
        root = owner(apiurl, package)
    return root

@memoize(session=True)
def maintainers_get(apiurl, project, package=None):
    if package is None:
        meta = ETL.fromstringlist(show_project_meta(apiurl, project))
        maintainers = meta.xpath('//person[@role="maintainer"]/@userid')

        groups = meta.xpath('//group[@role="maintainer"]/@groupid')
        maintainers.extend(groups_members(apiurl, groups))

        return maintainers

    # Ugly reparse, but real xpath makes the rest much cleaner.
    root = owner_fallback(apiurl, project, package)
    root = ETL.fromstringlist(ET.tostringlist(root))
    maintainers = root.xpath('//person[@role="maintainer"]/@name')

    groups = root.xpath('//group[@role="maintainer"]/@name')
    maintainers.extend(groups_members(apiurl, groups))

    return maintainers

@memoize(session=True)
def package_list(apiurl, project):
    url = makeurl(apiurl, ['source', project], { 'expand': 1 })
    root = ET.parse(http_GET(url)).getroot()

    packages = []
    for package in root.findall('entry'):
        packages.append(package.get('name'))

    return sorted(packages)

@memoize(session=True)
def target_archs(apiurl, project, repository='standard'):
    meta = ETL.fromstringlist(show_project_meta(apiurl, project))
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
    binary_map = {} # last duplicate wins
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

def request_age(request):
    if isinstance(request, Request):
        created = request.statehistory[0].when
    else:
        created = request.find('history').get('when')
    created = date_parse(created)
    return datetime.utcnow() - created

def project_list_prefix(apiurl, prefix):
    """Get a list of project with the same prefix."""
    query = {'match': 'starts-with(@name, "{}")'.format(prefix)}
    url = makeurl(apiurl, ['search', 'project', 'id'], query)
    root = ETL.parse(http_GET(url)).getroot()
    return root.xpath('project/@name')

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
        return http_GET(url).read()
    except HTTPError:
        return None

def source_file_save(apiurl, project, package, filename, content, comment=None):
    if not comment:
        comment = 'update by OSRT tools'
    comment += ' (host {})'.format(socket.gethostname())

    url = makeurl(apiurl, ['source', project, package, filename], {'comment': comment})
    http_PUT(url, data=content)

def source_file_ensure(apiurl, project, package, filename, content,  comment=None):
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
# multibuild entries nor linked packages.
def package_list_without_links(apiurl, project):
    query = {
        'view': 'info',
        'nofilename': '1',
    }
    url = makeurl(apiurl, ['source', project], query)
    root = ETL.parse(http_GET(url)).getroot()
    return root.xpath(
        '//sourceinfo[not(./linked[@project="{}"]) and not(contains(@package, ":"))]/@package'.format(project))

def attribute_value_load(apiurl, project, name, namespace='OSRT'):
    url = makeurl(apiurl, ['source', project, '_attribute', namespace + ':' + name])

    try:
        root = ETL.parse(http_GET(url)).getroot()
    except HTTPError as e:
        if e.code == 404:
            return None

        raise e

    value = root.xpath(
        './attribute[@namespace="{}" and @name="{}"]/value/text()'.format(namespace, name))
    if not len(value):
        return None

    return str(value[0])

# New attributes must be defined manually before they can be used. Example:
#   `osc api /attribute/OSRT/IgnoredIssues/_meta outputs`
#
# The new attribute can be created via:
#   `api -T $xml /attribute/OSRT/$NEWATTRIBUTE/_meta`
#
# Remember to create for both OBS and IBS as necessary.
def attribute_value_save(apiurl, project, name, value, namespace='OSRT'):
    root = ET.Element('attributes')

    attribute = ET.SubElement(root, 'attribute')
    attribute.set('namespace', namespace)
    attribute.set('name', name)

    ET.SubElement(attribute, 'value').text = value

    # The OBS API of attributes is super strange, POST to update.
    url = makeurl(apiurl, ['source', project, '_attribute'])
    http_POST(url, data=ET.tostring(root))

@memoize(session=True)
def repository_path_expand(apiurl, project, repo, repos=None):
    """Recursively list underlying projects."""

    if repos is None:
        # Avoids screwy behavior where list as default shares reference for all
        # calls which effectively means the list grows even when new project.
        repos = []

    if [project, repo] in repos:
        # For some reason devel projects such as graphics include the same path
        # twice for openSUSE:Factory/snapshot. Does not hurt anything, but
        # cleaner not to include it twice.
        return repos

    repos.append([project, repo])

    meta = ET.fromstringlist(show_project_meta(apiurl, project))
    for path in meta.findall('.//repository[@name="{}"]/path'.format(repo)):
        repository_path_expand(apiurl, path.get('project', project), path.get('repository'), repos)

    return repos

@memoize(session=True)
def repository_path_search(apiurl, project, search_project, search_repository):
    queue = []

    # Initialize breadth first search queue with repositories from top project.
    root = ETL.fromstringlist(show_project_meta(apiurl, project))
    for repository in root.xpath('repository[path[@project and @repository]]/@name'):
        queue.append((repository, project, repository))

    # Perform a breadth first search and return the first repository chain with
    # a series of path elements targeting search project and repository.
    for repository_top, project, repository in queue:
        if root.get('name') != project:
            # Repositories for a single project are in a row so cache parsing.
            root = ETL.fromstringlist(show_project_meta(apiurl, project))

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

    root = ETL.fromstringlist(show_results_meta(
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
