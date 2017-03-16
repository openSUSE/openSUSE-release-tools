from osc.core import get_request
from osclib.request_finder import RequestFinder


class UnselectCommand(object):

    def __init__(self, api):
        self.api = api

    def perform(self, packages):
        """
        Remove request from staging project
        :param packages: packages/requests to delete from staging projects
        """

        ignored_requests = self.api.get_ignored_requests()
        affected_projects = set()
        for request, request_project in RequestFinder.find_staged_sr(packages,
                                                                     self.api).items():
            staging_project = request_project['staging']
            affected_projects.add(staging_project)
            msg = 'Unselecting "{}" from "{}"'.format(request, staging_project)
            print(msg)
            self.api.rm_from_prj(staging_project, request_id=request, msg='Removing from {}, re-evaluation needed'.format(staging_project))
            self.api.add_review(request, by_group=self.api.cstaging_group, msg='Requesting new staging review')

            req = get_request(self.api.apiurl, str(request))
            if req.state.name in ('new', 'review') and request not in ignored_requests:
                print('  Consider marking the request ignored to let others know not to restage.')

        # Notify everybody about the changes
        for prj in affected_projects:
            meta = self.api.get_prj_pseudometa(prj)
            if len(meta['requests']) == 0:
                # Cleanup like accept since the staging is now empty.
                self.api.staging_deactivate(prj)
            else:
                self.api.update_status_comments(prj, 'unselect')
