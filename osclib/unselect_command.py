from osc import oscerr
from osc.core import http_GET

from osclib.request_finder import RequestFinder


class UnselectCommand(object):

    def __init__(self, api):
        self.api = api

    def perform(self, packages):
        """
        Remove request from staging project
        :param packages: packages/requests to delete from staging projects
        """
        for request, request_project in RequestFinder.find_staged_sr(packages, self.api).items():
            staging_project = request_project['staging']
            print('Unselecting "{}" from "{}"'.format(request, staging_project))
            self.api.rm_from_prj(staging_project, request_id=request)
            self.api.add_review(request, by_group='factory-staging', msg='Please recheck')
