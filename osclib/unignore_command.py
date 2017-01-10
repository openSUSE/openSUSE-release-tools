from osc.core import get_request


class UnignoreCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, request_ids):
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

        diff = length - len(requests_ignored)
        if diff > 0:
            print('Unignoring {} requests'.format(diff))
            self.api.set_ignored_requests(requests_ignored)
        else:
            print('No requests to unignore')

        return True
