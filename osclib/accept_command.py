import re
from xml.etree import cElementTree as ET

from osc.core import change_request_state
from osc.core import http_GET, http_PUT
from datetime import date
from osclib.comments import CommentAPI


class AcceptCommand(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)

    def find_new_requests(self, project):
        query = "match=state/@name='new'+and+(action/target/@project='{}'+and+action/@type='submit')".format(project)
        url = self.api.makeurl(['search', 'request'], query)

        f = http_GET(url)
        root = ET.parse(f).getroot()

        ids = []
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
            return False

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
        changed = False
        for req in self.find_new_requests('openSUSE:{}'.format(self.api.opensuse)):
            print "accepting request %s"%str(req)
            change_request_state(self.api.apiurl, str(req), 'accepted', message='Accept to factory')
            changed = True

        return changed

    def update_factory_version(self):
        """Update openSUSE (Factory, 13.2, ...)  version if is necessary."""
        project = 'openSUSE:{}'.format(self.api.opensuse)
        url = self.api.makeurl(['source', project, '_product', 'openSUSE.product'])

        product = http_GET(url).read()
        curr_version = date.today().strftime('%Y%m%d')
        new_product = re.sub(r'<version>\d{8}</version>', '<version>%s</version>' % curr_version, product)

        if product != new_product:
            http_PUT(url + "?comment=Update+version", data=new_product)
