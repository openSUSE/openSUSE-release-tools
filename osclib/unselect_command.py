from osc import conf
from osc.core import get_request
from osclib.request_finder import RequestFinder


class UnselectCommand(object):
    CLEANUP_WHITELIST = 'leaper'

    def __init__(self, api):
        self.api = api
        self.config_init(api)

    @classmethod
    def config_init(cls, api):
        config = conf.config[api.project]

        cleanup_whitelist = config.get('unselect-cleanup-whitelist', cls.CLEANUP_WHITELIST)
        cls.cleanup_whitelist = cleanup_whitelist.split()

        cls.cleanup_days = int(config.get('unselect-cleanup-days', 7))

    @staticmethod
    def filter_obsolete(request, updated_delta):
        if request['state'] == 'superseded':
            # Allow for cases where a request is superseded, but a newer request
            # is never staged due all newer requests being superseded/declined.
            return updated_delta.days >= UnselectCommand.cleanup_days

        if (request['state'] == 'revoked' or
           (request['state'] == 'declined' and (
                request['creator'] in UnselectCommand.cleanup_whitelist or
                updated_delta.days >= UnselectCommand.cleanup_days))):
            return True

        return False

    def perform(self, packages, cleanup=False):
        """
        Remove request from staging project
        :param packages: packages/requests to delete from staging projects
        """

        if cleanup:
            obsolete = self.api.project_status_requests('obsolete', self.filter_obsolete)
            if len(obsolete) > 0:
                print('Cleanup {} obsolete requests'.format(len(obsolete)))
                packages += tuple(obsolete)

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
            self.api.update_status_or_deactivate(prj, 'unselect')
