from collections import namedtuple
from datetime import datetime
import dateutil.parser
import re
from xml.etree import cElementTree as ET
from urllib2 import HTTPError

from osc.core import get_binarylist
from osc.core import get_dependson
from osc.core import http_GET
from osc.core import makeurl
from osc.core import owner
from osc.core import show_package_meta
from osc.core import show_project_meta
from osclib.memoize import memoize

BINARY_REGEX = r'(?:.*::)?(?P<filename>(?P<name>.*?)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^-\.]+))'
RPM_REGEX = BINARY_REGEX + '\.rpm'
BinaryParsed = namedtuple('BinaryParsed', ('package', 'filename', 'name', 'arch'))


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
        meta = ET.fromstring(''.join(show_project_meta(apiurl, project)))
        return [p.get('userid') for p in meta.findall('.//person') if p.get('role') == 'maintainer']

    root = owner_fallback(apiurl, project, package)
    maintainers = [p.get('name') for p in root.findall('.//person') if p.get('role') == 'maintainer']
    if not maintainers:
        for group in [p.get('name') for p in root.findall('.//group') if p.get('role') == 'maintainer']:
            url = makeurl(apiurl, ('group', group))
            root = ET.parse(http_GET(url)).getroot()
            maintainers = maintainers + [p.get('userid') for p in root.findall('./person/person')]
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
def target_archs(apiurl, project):
    meta = show_project_meta(apiurl, project)
    meta = ET.fromstring(''.join(meta))
    archs = []
    for arch in meta.findall('repository[@name="standard"]/arch'):
        archs.append(arch.text)
    return archs

@memoize(session=True)
def depends_on(apiurl, project, repository, packages=None, reverse=None):
    dependencies = set()
    for arch in target_archs(apiurl, project):
        root = ET.fromstring(get_dependson(apiurl, project, repository, arch, packages, reverse))
        dependencies.update(pkgdep.text for pkgdep in root.findall('.//pkgdep'))

    return dependencies

def request_when_staged(request, project, first=False):
    when = None
    for history in request.statehistory:
        if project in history.comment:
            when = history.when

    return dateutil.parser.parse(when)

def request_staged(request):
    for review in request.reviews:
        if (review.state == 'new' and review.by_project and
            review.by_project.startswith(request.actions[0].tgt_project)):

            # Allow time for things to settle.
            when = request_when_staged(request, review.by_project)
            if (datetime.utcnow() - when).total_seconds() > 10 * 60:
                return review.by_project

    return None

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
def package_binary_list(apiurl, project, repository, arch, package=None):
    path = ['build', project, repository, arch]
    if package:
        path.append(package)
    url = makeurl(apiurl, path, {'view': 'binaryversions'})
    root = ET.parse(http_GET(url)).getroot()

    package_binaries = []
    binary_map = {} # last duplicate wins
    for binary_list in root:
        # Strip off multibuild extra to provide actual package name. The full
        # value may be useful for duplicate check.
        package = binary_list.get('package').split(':', 1)[0]
        for binary in binary_list:
            filename = binary.get('name')
            result = re.match(RPM_REGEX, filename)
            if not result:
                continue

            package_binaries.append(BinaryParsed(package, result.group('filename'),
                                                 result.group('name'), result.group('arch')))
            binary_map[result.group('filename')] = package

    return package_binaries, binary_map

@memoize(session=True)
def devel_project_get(apiurl, target_project, target_package):
    try:
        meta = ET.fromstring(''.join(show_package_meta(apiurl, target_project, target_package)))
        node = meta.find('devel')
        if node is not None:
            return node.get('project'), node.get('package')
    except HTTPError as e:
        if e.code != 404:
            raise e

    return None, None
