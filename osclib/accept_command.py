from osc.core import change_request_state


class AcceptCommand:
    def __init__(self, api):
        self.api = api

    def perform(self, project):
        """
        Accept the staging LETTER for review and submit to factory
        Then disable the build to disabled
        :param project: staging project we are working with
        """
        status = self.api.check_project_status(project)

        if not status:
            print('The project "{0}" is not yet acceptable.'.format(project))
            return

        meta = self.api.get_prj_pseudometa(project)
        requests = []
        for req in meta['requests']:
            self.api.rm_from_prj(project, request_id=req['id'], msg='ready to accept')
            print('Accepting staging review for {0}'.format(req['package']))
            requests.append(req['id'])

        for req in requests:
            change_request_state(self.api.apiurl, str(req), 'accepted', message='Accept to factory')

        self.api.build_switch_prj(project, 'disable')
