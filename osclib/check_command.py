from xml.etree import cElementTree as ET

from osc.core import makeurl
from osc.core import http_GET


class CheckCommand(object):
    def __init__(self, api):
        self.api = api

    def _check_one_project(self, project, verbose):
        """
        Check state of one specified staging project
        :param project: project to check
        :param verbose: do verbose check or not
        """
        state = self.api.check_project_status(project, verbose)

        # If the project is empty just skip it
        if not state:
            return False

        print('Checking staging project: {}'.format(project))
        if type(state) is list:
            print(' -- Project still neeeds attention')
            for issue in state:
                print(issue)
        else:
            print(' ++ Acceptable staging project')

        return True

    def perform(self, project):
        """
        Check one staging project verbosibly or all of them at once
        :param project: project to check, None for all
        """
        if project:
            self._check_one_project(project, True)
        else:
            for project in self.api.get_staging_projects():
                if self._check_one_project(project, False):
                    # newline to split multiple prjs at once
                    print('')

        return True
