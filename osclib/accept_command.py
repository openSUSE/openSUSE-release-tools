from osc.core import change_request_state
from osc.core import get_request

from osclib.comments import CommentAPI


class AcceptCommand(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)

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
            msg = 'Accepting staging review for {0}'.format(req['package'])
            print(msg)

            # Write a comment in the project.
            user = get_request(self.api.apiurl, str(req['id'])).get_creator()
            self.comment.add_comment(project_name=project, comment='@%s: %s' % (user, msg))

            requests.append(req['id'])

        for req in requests:
            change_request_state(self.api.apiurl, str(req), 'accepted', message='Accept to factory')

        # XXX CAUTION - AFAIK the 'accept' command is expected to clean the messages here.
        self.comment.delete_from(project_name=project)

        self.api.build_switch_prj(project, 'disable')
