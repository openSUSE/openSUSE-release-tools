
import logging
import re

import osc.core
import osc.conf

from lxml import etree as ET
from urllib.error import HTTPError, URLError

class RemotePackagesReader(object):
    """ This service class reads the packages for a given project

    The idea is to extract the logic to read the packages from their representation and avoid
    coupling our model to the build service API.
    """
    def from_project(self, project_name, apiurl):
        """ Returns the list of packages from the build service for a given project """
        url = osc.core.makeurl(apiurl, ['source', project_name], {'view': 'info'})
        return self.from_string(osc.core.http_GET(url))

    def from_string(self, string):
        """ Return a list of packages from the string """
        xml_tree = ET.parse(string)
        root = xml_tree.getroot()
        packages = [self._sourceinfo_to_package(s) for s in root]
        return list(filter(None, packages))

    def _sourceinfo_to_package(self, sourceinfo):
        """ Turns a sourceinfo element into a package if possible """
        name = sourceinfo.get('package')
        if not self._is_package(name):
            return None

        linked = {l.get('project'): l.get('package') for l in sourceinfo.findall('linked')}
        return RemotePackage(
            name=sourceinfo.get('package'),
            rev=sourceinfo.get('rev'),
            origin=sourceinfo.findtext('originproject'),
            linked=linked
        )

    IGNORED_PKG_PREFIXES = ('00', '000', '_', 'patchinfo.', 'skelcd-', 'installation-images', 'kernel-livepatch-')
    IGNORED_PKG_SUFFIXES = ('-mini')
    INCIDENT_REGEXP = re.compile(r'.+\.\d+$')

    def _is_package(self, name):
        """ Determines whether it is a valid package (exclude incidents, updates and so on) """
        if name.startswith(self.IGNORED_PKG_PREFIXES):
            return False

        if name.endswith(self.IGNORED_PKG_SUFFIXES):
            return False

        if self.INCIDENT_REGEXP.match(name):
            if not name.startswith(('go1', 'bazel0', 'dotnet', 'ruby2')) or name.count('.') > 1:
                return False

        return True

class RemotePackage(object):
    """ This class represents a package on the build service side """
    def __init__(self, name, rev, origin, linked={}):
        self.name = name
        self.rev = rev
        self.origin = origin
        self.linked = linked

    def copy(self, target_project_name, expand=False):
        apiurl = osc.conf.config['apiurl']
        osc.core.copy_pac(apiurl, self.project_name, self.name, apiurl, target_project_name, self.name, expand=expand)
        return RemotePackage(self.name, target_project_name)

    def link(self, target_project_name):
        osc.core.link_pac(self.project_name, self.name, target_project_name, self.name,
                          force=True, rev=self.rev)
        return RemotePackage(self.name, target_project_name)

if __name__ == '__main__':
    reader = RemotePackagesReader()

    # with open("sle-15-sp3-update.xml") as f:
    #     packages = reader.from_string(f)

    osc.conf.get_config('oscrc')
    packages = reader.from_project("SUSE:SLE-15-SP3:Update", osc.conf.config['apiurl'])

    for pkg in packages:
        print("%s rev:%s" % (pkg.name, pkg.rev))
    print("%d packages" % len(packages))
