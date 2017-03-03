import dateutil.parser
from datetime import datetime

from osc.core import get_request


class UnignoreCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, request_ids, cleanup=False):
        """
        Unignore a request by removing from ignore list.
        """

        requests_ignored = self.api.get_ignored_requests()
        length = len(requests_ignored)

        if len(request_ids) == 1 and request_ids[0] == 'all':
            requests_ignored = {}
        else:
            for request_id in request_ids:
                request_id = int(request_id)
                if request_id in requests_ignored:
                    print('Removing {}'.format(request_id))
                    del requests_ignored[request_id]

        if cleanup:
            now = datetime.now()
            for request_id in set(requests_ignored):
                request = get_request(self.api.apiurl, str(request_id))
                if request.state.name not in ('new', 'review'):
                    changed = dateutil.parser.parse(request.state.when)
                    diff = now - changed
                    if diff.days > 3:
                        print('Removing {} which was {} {} days ago'
                              .format(request_id, request.state.name, diff.days))
                        del requests_ignored[request_id]

        diff = length - len(requests_ignored)
        if diff > 0:
            print('Unignoring {} requests'.format(diff))
            self.api.set_ignored_requests(requests_ignored)
        else:
            print('No requests to unignore')

        return True
