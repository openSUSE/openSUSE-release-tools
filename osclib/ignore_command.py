from osc.core import get_request
from osclib.comments import CommentAPI
from osclib.request_finder import RequestFinder


class IgnoreCommand(object):
    MESSAGE = 'Ignored: removed from active backlog.'

    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)

    def perform(self, requests, message=None):
        """
        Ignore a request from "list" and "adi" commands until unignored.
        """

        requests_ignored = self.api.get_ignored_requests()
        length = len(requests_ignored)

        for request_id in RequestFinder.find_sr(requests, self.api):
            if request_id in requests_ignored:
                print('{}: already ignored'.format(request_id))
                continue

            print('{}: ignored'.format(request_id))
            requests_ignored[request_id] = message
            comment = message if message else self.MESSAGE
            self.comment.add_comment(request_id=str(request_id), comment=comment)

        diff = len(requests_ignored) - length
        if diff > 0:
            self.api.set_ignored_requests(requests_ignored)
            print('Ignored {} requests'.format(diff))
        else:
            print('No new requests to ignore')

        return True
