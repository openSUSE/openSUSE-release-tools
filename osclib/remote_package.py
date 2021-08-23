
import logging
import osc.core
import osc.conf

from lxml import etree as ET
from urllib.error import HTTPError, URLError

class PackageNotFound(Exception):
    """ The package was not found in the build service """
    pass


class PackageMetadata(object):
    """ It holds package meta information

    NOTE: to be extended with more elements. Otherwise, it could live within RemotePackage.
    """
    def __init__(self, releasename=None):
        self.releasename = releasename

    @classmethod
    def from_xml_node(cls, node):
        return PackageMetadata(
            releasename=node.findtext('releasename')
        )

    @classmethod
    def load(cls, project_name, package_name):
        url = osc.core.make_meta_url('pkg', (project_name, package_name), osc.conf.config['apiurl'])
        try:
            xml_tree = ET.parse(osc.core.http_GET(url))
            node = xml_tree.getroot()
            return cls.from_xml_node(node)
        except HTTPError as e:
            if e.code == 404:
                raise PackageNotFound("Package %s/%s not found" % (project_name, package_name))
            else:
                raise

class RemotePackage(object):
    """ This class represents a package on the build service side """
    def __init__(self, name, project=None, metadata=None):
        self.name = name
        self.project_name = project
        self._metadata = metadata

    def metadata(self):
        if self._metadata:
            return self._metadata

        self._metadata = PackageMetadata.load(self.project_name, self.name)
        return self._metadata

    def copy(self, target_project_name, expand=False):
        apiurl = osc.conf.config['apiurl']
        osc.core.copy_pac(apiurl, self.project_name, self.name, apiurl, target_project_name, self.name, expand=expand)
        return RemotePackage(self.name, target_project_name)

    def link(self, target_project_name):
        osc.core.link_pac(self.project_name, self.name, target_project_name, self.name, force=False)
        return RemotePackage(self.name, target_project_name)

    def releasename(self):
        """ Returns the releasename for the package

        If it is not explictly defined as part of the metadata, just return the package's name.
        """
        if self.metadata():
            return self.metadata().releasename

        return self.name

    @classmethod
    def from_xml(cls, xml_tree):
        """ Returns a project from an XML node (lxml.etree._ElementTree) """
        node = xml_tree.getroot()

        return RemotePackage(
            node.get('name'),
            project=node.get('project'),
            metadata=PackageMetadata.from_xml_node(node)
        )

    @classmethod
    def find(cls, project_name, package_name):
        """ Returns a package from the build service

        :raises:
          PackageNotFound: if the package is not found in the given project
        """
        url = osc.core.make_meta_url('pkg', (project_name, package_name), osc.conf.config['apiurl'])
        try:
            xml_tree = ET.parse(osc.core.http_GET(url))
            return cls.from_xml(xml_tree)
        except HTTPError as e:
            if e.code == 404:
                raise PackageNotFound('Package %s not found' % (package_name))
