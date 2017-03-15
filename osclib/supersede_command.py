
class SupersedeCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, packages=None):
        self.api.dispatch_open_requests(packages)
