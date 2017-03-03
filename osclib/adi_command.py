import json

from osc import oscerr
from osc.core import delete_project
from osc.core import show_package_meta

from osclib.select_command import SelectCommand
from osclib.request_finder import RequestFinder
from osclib.request_splitter import RequestSplitter
from xml.etree import cElementTree as ET

class AdiCommand:
    def __init__(self, api):
        self.api = api

    def check_adi_project(self, project):
        query_project = 'adi:' + project.split(':adi:')[1]
        query = {'format': 'json'}
        url = self.api.makeurl(('project', 'staging_projects', self.api.project,
                                query_project), query=query)
        info = json.load(self.api.retried_GET(url))
        if len(info['building_repositories']):
            print query_project, "still building"
            return
        if len(info['untracked_requests']):
            print query_project, "untracked:", ', '.join(['%s[%s]'%(req['package'], req['number']) for req in info['untracked_requests']])
            return
        if len(info['obsolete_requests']):
            print query_project, "obsolete:", ', '.join(['%s[%s]'%(req['package'], req['number']) for req in info['obsolete_requests']])
            return
        if len(info['broken_packages']):
            print query_project, "broken:", ', '.join([p['package'] for p in info['broken_packages']])
            return
        for review in info['missing_reviews']:
            print query_project, "missing review by {} for {}[{}]".format(review['by'], review['package'], review['request'])
            return
        if self.api.is_user_member_of(self.api.user, 'factory-staging'):
            print query_project, "is ready"
            for req in info['selected_requests']:
                print " - %s [%s]"%(req['package'], req['number'])
                self.api.rm_from_prj(project, request_id=req['number'], msg='ready to accept')
            delete_project(self.api.apiurl, project)
        else:
            print query_project, "ready:", ', '.join(['%s[%s]'%(req['package'], req['number']) for req in info['selected_requests']])

    def check_adi_projects(self):
        for p in self.api.get_adi_projects():
            self.check_adi_project(p)

    def create_new_adi(self, wanted_requests, by_dp=False, split=False):
        requests = self.api.get_open_requests()
        splitter = RequestSplitter(self.api, requests, in_ring=False)
        splitter.filter_add('./action[@type="submit"]')
        if len(wanted_requests):
            splitter.filter_add_requests([str(p) for p in wanted_requests])
            # wanted_requests forces all requests into a single group.
        else:
            if split:
                splitter.group_by('./@id')
            elif by_dp:
                splitter.group_by('./action/target/@devel_project')
            else:
                splitter.group_by('./action/source/@project')
        splitter.split()

        for group in sorted(splitter.grouped.keys()):
            print(group if group != '' else 'wanted')

            name = None
            for request in splitter.grouped[group]['requests']:
                request_id = int(request.get('id'))
                target_package = request.find('./action/target').get('package')
                line = '- sr#{}: {:<30}'.format(request_id, target_package)

                if request_id in self.requests_ignored:
                    print(line + '\n    ignored: ' + str(self.requests_ignored[request_id]))
                    continue

                # Auto-superseding request in adi command
                if self.api.update_superseded_request(request):
                    print(line + ' (superseded)')
                    continue

                # Only create staging projec the first time a non superseded
                # request is processed from a particular group.
                if name is None:
                    name = self.api.create_adi_project(None)

                if not self.api.rq_to_prj(request_id, name):
                    return False

                print(line + ' (staged in {})'.format(name))

            if name:
                # Notify everybody about the changes.
                self.api.update_status_comments(name, 'select')

    def perform(self, packages, move=False, by_dp=False, split=False):
        """
        Perform the list command
        """
        self.requests_ignored = self.api.get_ignored_requests()
        if len(packages):
            requests = set()
            if move:
                items = RequestFinder.find_staged_sr(packages, self.api).items()
                print items
                for request, request_project in items:
                    staging_project = request_project['staging']
                    self.api.rm_from_prj(staging_project, request_id=request)
                    self.api.add_review(request, by_group=self.api.cstaging_group, msg='Please recheck')
            else:
                items = RequestFinder.find_sr(packages, self.api).items()

            for request, request_project in items:
                requests.add(request)
            self.create_new_adi(requests, split=split)
        else:
            self.check_adi_projects() 
            if self.api.is_user_member_of(self.api.user, 'factory-staging'):
                self.create_new_adi((), by_dp=by_dp, split=split)
