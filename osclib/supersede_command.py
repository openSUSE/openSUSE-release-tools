
class SupersedeCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, requests=None):
        for stage_info, request in self.api.dispatch_open_requests(requests):
            action = request.find('action')
            target_package = action.find('target').get('package')
            print('request {} for {} superseded {} in {}'.format(
                request.get('id'), target_package, stage_info['rq_id'], stage_info['prj']))
