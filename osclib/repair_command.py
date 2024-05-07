from urllib.error import HTTPError
from osc import oscerr
from osc.core import change_review_state
from osc.core import get_request
from osclib.request_finder import RequestFinder


class RepairCommand(object):

    def __init__(self, api):
        self.api = api

    def repair(self, request):
        reviews = []
        reqid = str(request)
        req = get_request(self.api.apiurl, reqid)

        if not req:
            raise oscerr.WrongArgs(f'Request {reqid} not found')

        if req.state.name != 'review':
            print(f'Request "{reqid}" is not in review state')
            return

        reviews = [r.by_project for r in req.reviews if ':Staging:' in str(r.by_project) and r.state == 'new']

        if reviews:
            if len(reviews) > 1:
                raise oscerr.WrongArgs(
                    f'Request {reqid} had multiple review opened by different staging project')
        else:
            raise oscerr.WrongArgs(f'Request {reqid} is not for staging project')

        staging_project = reviews[0]
        try:
            data = self.api.project_status(staging_project)
        except HTTPError as e:
            if e.code == 404:
                data = None

        # Pre-check and pre-setup
        if data is not None:
            for request in data.findall('staged_requests/requests'):
                if request.get('id') == reqid:
                    print(f'Request "{reqid}" had the good setup in "{staging_project}"')
                    return
        else:
            # this situation should only happen on adi staging
            print(f'Project is not exist, re-creating "{staging_project}"')
            self.api.create_adi_project(staging_project)

        # a bad request setup found
        print(f'Repairing "{reqid}"')
        change_review_state(self.api.apiurl, reqid, newstate='accepted',
                            message='Re-evaluation needed', by_project=staging_project)
        self.api.add_review(reqid, by_group=self.api.cstaging_group, msg='Requesting new staging review')

    def perform(self, packages, cleanup=False):
        """
        Repair request in staging project or move it out
        :param packages: packages/requests to repair in staging projects
        """

        if cleanup:
            untracked = self.api.project_status_requests('untracked')
            if len(untracked) > 0:
                print(f'Cleanup {len(untracked)} untracked requests')
                packages += tuple(untracked)

        for reqid in RequestFinder.find_sr(packages, self.api):
            self.repair(reqid)
