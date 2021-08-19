
import logging
import osc.core
import osc.conf

from lxml import etree as ET
from urllib.error import HTTPError, URLError

class PackageNotFound(Exception):
    """ The package was not found in the build service """
    pass

class RemotePackage(object):
    """ This class represents a package on the build service side """
    def __init__(self, name, project=None):
        self.name = name
        self.project_name = project

    @classmethod
    def from_xml(cls, content):
        """ Returns a project from an XML node (lxml.etree._ElementTree) """
        data = ET.parse(content)
        node = data.getroot()
        return RemotePackage(node.get('name'), project=node.get('project'))

    @classmethod
    def find(cls, project_name, package_name):
        """ Returns a package from the build service

        :raises:
          PackageNotFound: if the package is not found in the given project
        """
        url = osc.core.make_meta_url('pkg', (project_name, package_name), osc.conf.config['apiurl'])
        try:
            return cls.from_xml(osc.core.http_GET(url))
        except HTTPError as e:
            if e.code == 404:
                raise PackageNotFound('Package %s not found' % (package_name))
