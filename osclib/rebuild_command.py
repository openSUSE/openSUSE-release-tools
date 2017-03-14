from osc.core import get_request
from osclib.comments import CommentAPI


class RebuildCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, stagings=None, force=False):
        if not stagings:
            stagings = self.api.get_staging_projects_short()

        for staging in stagings:
            status = self.api.project_status(staging)
            rebuilt = self.api.rebuild_broken(status, not force)
            for key, code in rebuilt.items():
                print('rebuild {} {}'.format(key, code))
