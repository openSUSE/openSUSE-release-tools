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

        for request_id in RequestFinder.find_sr(requests, self.api):
            print(f'{request_id}: ignored')
            comment = message if message else self.MESSAGE
            self.api.add_ignored_request(request_id, comment)
            self.comment.add_comment(request_id=str(request_id), comment=comment)

        return True
