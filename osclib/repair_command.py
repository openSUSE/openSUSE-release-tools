from __future__ import print_function

import re
import urllib2

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
            raise oscerr.WrongArgs('Request {} not found'.format(reqid))

        if req.state.name != 'review':
            print('Request "{}" is not in review state'.format(reqid))
            return

        reviews = [r.by_project for r in req.reviews if ':Staging:' in str(r.by_project) and r.state == 'new']

        if reviews:
            if len(reviews) > 1:
                raise oscerr.WrongArgs('Request {} had multiple review opened by different staging project'.format(reqid))
        else:
            raise oscerr.WrongArgs('Request {} is not for staging project'.format(reqid))

        staging_project = reviews[0]
        try:
            data = self.api.get_prj_pseudometa(staging_project)
        except urllib2.HTTPError, e:
            if e.code == 404:
                data = None

        # Pre-check and pre-setup
        if data:
            for request in data['requests']:
                if request['id'] == reqid:
                    print('Request "{}" had the good setup in "{}"'.format(reqid, staging_project))
                    return
        else:
            # this situation should only happens on adi staging
            print('Project is not exist, re-creating "{}"'.format(staging_project))
            name = self.api.create_adi_project(staging_project)

        # a bad request setup found
        print('Repairing "{}"'.format(reqid))
        change_review_state(self.api.apiurl, reqid, newstate='accepted', message='Re-evaluation needed', by_project=staging_project)
        self.api.add_review(reqid, by_group=self.api.cstaging_group, msg='Requesting new staging review')

    def perform(self, packages):
        """
        Repair request in staging project or move it out
        :param packages: packages/requests to repair in staging projects
        """

        for reqid in RequestFinder.find_sr(packages, self.api):
            self.repair(reqid)
