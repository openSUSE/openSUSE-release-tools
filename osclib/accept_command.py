from osc.core import change_request_state
from osc.core import get_request
from osc.core import http_GET

from osclib.comments import CommentAPI

from xml.etree import cElementTree as ET

class AcceptCommand(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)

    def find_new_requests(self, project):
        requests = []

        query = "match=state/@name='new'+and+(action/target/@project='{}'+and+action/@type='submit')".format(project)
        url = self.api.makeurl(['search', 'request'], query)

        f = http_GET(url)
        root = ET.parse(f).getroot()
        
        ids=[]
        for rq in root.findall('request'):
            ids.append(int(rq.get('id')))
        return ids
            
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
        packages = []
        for req in meta['requests']:
            self.api.rm_from_prj(project, request_id=req['id'], msg='ready to accept')
            requests.append(req['id'])
            packages.append(req['package'])
            msg = 'Accepting staging review for {0}'.format(req['package'])
            print(msg)

        for req in requests:
            change_request_state(self.api.apiurl, str(req), 'accepted', message='Accept to factory')

        # A single comment should be enough to notify everybody, since they are
        # already mentioned in the comments created by select/unselect
        pkg_list = ", ".join(packages)
        cmmt = 'Project "{0}" accepted. The following packages have been submitted to factory: {1}.'.format(project, pkg_list)
        self.comment.add_comment(project_name=project, comment=cmmt)

        # XXX CAUTION - AFAIK the 'accept' command is expected to clean the messages here.
        self.comment.delete_from(project_name=project)

        self.api.build_switch_prj(project, 'disable')
        if self.api.project_exists(project + ":DVD"):
            self.api.build_switch_prj(project + ":DVD", 'disable')

        return True

    def accept_other_new(self):
        for req in self.find_new_requests('openSUSE:Factory'):
            change_request_state(self.api.apiurl, str(req), 'accepted', message='Accept to factory')

        return True
