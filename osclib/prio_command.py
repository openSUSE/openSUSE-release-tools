import osc

from urllib.error import HTTPError


class PrioCommand(object):
    def __init__(self, api):
        self.api = api

    def _setprio(self, status, priority):
        """
        Set prios for requests that are still in review
        :param project: project to check

        """
        message = f"raising priority for {status.get('name')}"
        for r in status.findall('missing_reviews/review'):
            reqid = r.get('request')
            req = osc.core.get_request(self.api.apiurl, reqid)
            if req.priority == priority:
                continue
            query = {'cmd': 'setpriority', 'priority': priority}
            url = osc.core.makeurl(self.api.apiurl, ['request', reqid], query)
            print(f"raising priority of {r.get('package')} [{r.get('request')}] to {priority}")
            try:
                osc.core.http_POST(url, data=message)
            except HTTPError as e:
                print(e)

    def perform(self, projects, priority):
        """
        Set priority on specific stagings or all of them at once
        :param projects: projects on which to set priority, None for all
        """
        if not priority:
            priority = 'important'

        for project in projects:
            project = self.api.prj_from_short(project)
            info = self.api.project_status(project, status=False)
            self._setprio(info, priority)

        return True
