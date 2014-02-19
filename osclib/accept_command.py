import osc
from osc import cmdln
from osc.core import *

class AcceptCommand:
    def __init__(self, api):
        self.api = api

    def perform(self, project):
        status = self.api.check_project_status(project)

        if not status:
            print "Make sure to fix the project first"
            return

        meta = self.api.get_prj_pseudometa(project)
        requests = []
        for req in meta['requests']:
            self.api.rm_from_prj(project, request_id=req['id'], msg='ready to accept')
            print 'accepting {}'.format(req['package'])
            requests.append(req['id'])

        for req in requests:
            change_request_state(self.api.apiurl, str(req), 'accepted', message='Accept to factory')

        self.api.build_switch_prj(project, 'disable')
