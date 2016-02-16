from osclib.request_finder import RequestFinder


class UnselectCommand(object):

    def __init__(self, api):
        self.api = api

    def perform(self, packages):
        """
        Remove request from staging project
        :param packages: packages/requests to delete from staging projects
        """

        affected_projects = set()
        for request, request_project in RequestFinder.find_staged_sr(packages,
                                                                     self.api).items():
            staging_project = request_project['staging']
            affected_projects.add(staging_project)
            msg = 'Unselecting "{}" from "{}"'.format(request, staging_project)
            print(msg)
            self.api.rm_from_prj(staging_project, request_id=request, msg='Removing from {}, re-evaluation needed'.format(staging_project))
            self.api.add_review(request, by_group=self.api.cstaging_group, msg='Requesting new staging review')

        # Notify everybody about the changes
        for prj in affected_projects:
            self.api.update_status_comments(prj, 'unselect')
