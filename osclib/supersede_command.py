
class SupersedeCommand(object):
    CODE_MAP = {
        None: 'superseded',
        True: 'declined',
        False: 'ignored',
    }

    def __init__(self, api):
        self.api = api

    def perform(self, requests=None):
        for stage_info, code, request in self.api.dispatch_open_requests(requests):
            action = request.find('action')
            target_package = action.find('target').get('package')
            verbage = self.CODE_MAP[code]
            if code is not None:
                verbage += ' in favor of'
            print('request {} for {} {} {} in {}'.format(
                request.get('id'), target_package, verbage,
                stage_info['rq_id'], stage_info['prj']))
