from osc.core import get_request
from osclib.comments import CommentAPI
from osclib.request_finder import RequestFinder


class IgnoreCommand(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)

    def perform(self, requests, message=None):
        """
        Ignore a request from "list" and "adi" commands until unignored.
        """

        requests_ignored = self.api.get_ignored_requests()
        length = len(requests_ignored)

        for request_id, _ in RequestFinder.find_staged_sr(requests, self.api).items():
            request_id = str(request_id)
            print('Processing {}'.format(request_id))
            check = self.check_and_comment(request_id, message)
            if check is not True:
                print('- {}'.format(check))
            elif request_id not in requests_ignored:
                requests_ignored[int(request_id)] = message

        diff = len(requests_ignored) - length
        if diff > 0:
            print('Ignoring {} requests'.format(diff))
            self.api.set_ignored_requests(requests_ignored)
        else:
            print('No new requests to ignore')

        return True

    def check_and_comment(self, request_id, message=None):
        request = get_request(self.api.apiurl, request_id)
        if not request:
            return 'not found'
        if request.actions[0].tgt_project != self.api.project:
            return 'not targeting {}'.format(self.api.project)
        if message:
            self.comment.add_comment(request_id=request_id, comment=message)

        return True
