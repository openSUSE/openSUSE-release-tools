
class SupersedeCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, requests=None):
        self.api.dispatch_open_requests(requests)
