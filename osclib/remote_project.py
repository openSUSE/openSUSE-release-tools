import logging

from lxml import etree as ET

import osc.core
import osc.conf

from urllib.error import HTTPError, URLError

class ProjectNotFound(Exception):
    """Raised when Project is not found on server"""
    pass

class RemoteProject(object):
    """This class represents a project on build service side.

    The class offers methods to query and modify the project.
    The class methods can be used to find or create projects.

    Not to be confused with the class Project in osc.core_, aimed to local checkout of project

    .. _osc.core: https://github.com/openSUSE/osc/blob/master/osc/core.py

    """
    def __init__(self, name):
        self.name = name
        self.metadata = None

    @classmethod
    def find(cls, name):
        """Raise ProjectNotFound if not found"""
        metadata = ProjectMetadata.load(name)
        res = cls(name)
        res.metadata = metadata

        return res

class ProjectMetadata(object):
    def __init__(self, linked_projects_names):
        self.linked_projects_names = linked_projects_names

    def linked_projects(self, recursive = False):
        to_process = self.linked_projects_names
        result = []
        while(to_process):
            name = to_process.pop(0)
            if (all([r.name != name for r in result])):
              project = RemoteProject.find(name)
              result.append(project)
              if recursive:
                  to_process += project.metadata.linked_projects_names

        return result

    @classmethod
    def parse(cls, content):
        data = ET.parse(content)
        linked_projects = []
        for child in data.getroot():
            if child.tag == 'link':
                linked_projects.append(child.attrib['project'])

        return cls(linked_projects)

    @classmethod
    def load(cls, project_name):
        url = osc.core.make_meta_url('prj', project_name, osc.conf.config['apiurl'])
        try:
            return cls.parse(osc.core.http_GET(url))
        except HTTPError as e:
            if e.code == 404:
                raise ProjectNotFound("Project %s not found" % (project_name))
            else:
                raise
