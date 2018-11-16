from __future__ import print_function

import json
import osc

try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

class PrioCommand(object):
    def __init__(self, api):
        self.api = api

    def _setprio(self, project, priority):
        """
        Set prios for requests that are still in review
        :param project: project to check

        """

        # XXX taking name verbatim would produce null byte error
        # https://github.com/openSUSE/open-build-service/issues/2493
        message = 'raising priority for %s'%str(project['name'])
        for r in project['missing_reviews']:
            reqid = str(r['request'])
            req = osc.core.get_request(self.api.apiurl, reqid)
            if req.priority != priority:
                query = { 'cmd': 'setpriority', 'priority': priority }
                url = osc.core.makeurl(self.api.apiurl, ['request', reqid], query)
                print(reqid + ' ' + message)
                try:
                    osc.core.http_POST(url, data=message)
                    print(reqid + ' ' + r['by'] + ' ' + priority)
                except HTTPError as e:
                    print(e)


    def perform(self, projects=None, priority=None):
        """
        Set priority on specific stagings or all of them at once
        :param projects: projects on which to set priority, None for all
        """

        aggregate = False
        if not projects:
            aggregate = True
            projects = self.api.get_staging_projects()

        if not priority:
            priority = 'important'

        for project in projects:
            info = self.api.project_status(project, aggregate)
            if not info['selected_requests']:
                continue
            self._setprio(info, priority)

        return True
