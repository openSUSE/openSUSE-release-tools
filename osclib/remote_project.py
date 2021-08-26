import logging
import re

from lxml import etree as ET

import osc.core
import osc.conf

import osclib.remote_package
from osclib.remote_package import RemotePackagesReader

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

    def create_subproject(self, name, title=None, description=None):
        """Creates subproject with given name. Title and description can be passed,
        rest of metadata is copied from self."""
        raw_metadata = ProjectMetadata.load(self.name)
        fullname = self.name + ":" + name
        root = ET.fromstring(raw_metadata)
        root.set('name', fullname)
        for child in root:
            if child.tag == "title" and title:
                child.text = title
            elif child.tag == "description" and description:
                child.text = description
        modified_metadata = ET.tostring(root)
        ProjectMetadata.save(fullname, modified_metadata)

        result = RemoteProject(fullname)
        result.metadata = ProjectMetadata.parse(modified_metadata)
        return result

    def get_packages(self):
        """Gets packages. If needed also inherited is included.
        Inherited just skips patchinfos and kernel live patches as it is usually not needed.
        """
        reader = RemotePackagesReader()
        return reader.from_project(self.name, osc.conf.config['apiurl'])

    @classmethod
    def find(cls, name):
        """Raise ProjectNotFound if not found"""
        metadata = ProjectMetadata.parse(ProjectMetadata.load(name))
        res = cls(name)
        res.metadata = metadata

        return res

class ProjectMetadata(object):
    def __init__(self, linked_projects_names):
        self.linked_projects_names = linked_projects_names

    def linked_projects(self, recursive = False):
        to_process = self.linked_projects_names.copy()
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
        """Parses metadata string passed in argument"""
        data = ET.fromstring(content)
        linked_projects = []
        for child in data:
            if child.tag == 'link':
                linked_projects.append(child.attrib['project'])

        return cls(linked_projects)

    @classmethod
    def load(cls, project_name):
        """Loads metadata as string from BS instance"""
        url = osc.core.make_meta_url('prj', project_name, osc.conf.config['apiurl'])
        try:
            return osc.core.http_GET(url).read()
        except HTTPError as e:
            if e.code == 404:
                raise ProjectNotFound("Project %s not found" % (project_name))
            else:
                raise

    @classmethod
    def save(cls, project_name, content):
        """Saves given content as project metadata"""
        url = osc.core.make_meta_url('prj', project_name, osc.conf.config['apiurl'])
        try:
            return osc.core.http_PUT(url, data=content)
        except HTTPError as e:
            # TODO: better error handling like detection of wrong content
            raise
