import json

from osc import oscerr
from osc.core import delete_project
from osc.core import show_package_meta

from osclib.select_command import SelectCommand
from osclib.request_finder import RequestFinder
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
        all_requests = self.api.get_open_requests()

        non_ring_packages = []
        non_ring_requests = dict()

        for request in all_requests:
            # Consolidate all data from request
            request_id = int(request.get('id'))
            if len(wanted_requests) and request_id not in wanted_requests:
                continue
            action = request.findall('action')
            if not action:
                msg = 'Request {} has no action'.format(request_id)
                raise oscerr.WrongArgs(msg)
            # we care only about first action
            action = action[0]

            # Where are we targeting the package
            if len(wanted_requests):
                source_project = 'wanted'
            else:
                source = action.find('source')
                if source is not None:
                    source_project = source.get('project')
                else:
                    source_project = 'none'

            # do not process the rest request type than submit
            if action.get('type') != 'submit':
                continue

            target_package = action.find('target').get('package')
            source_package = action.find('source').get('package')

            if not self.api.ring_packages.get(target_package):
                # Auto-superseding request in adi command
                if self.api.update_superseded_request(request):
                    continue

                non_ring_packages.append(target_package)
                if split:
                    # request_id pretended to be index of non_ring_requests
                    non_ring_requests[request_id] = [request_id]
                else:
                    if by_dp:
                        devel_project = self.api.get_devel_project(source_project, source_package)
                        # try target pacakge in Factory
                        # this is a bit against Leap development in case submissions is from Update,
                        # or any other project than Factory
                        if devel_project is None and self.api.project.startswith('openSUSE:'):
                            devel_project = self.api.get_devel_project('openSUSE:Factory', target_package)
                        if devel_project is not None:
                            source_project = devel_project

                    if source_project not in non_ring_requests:
                        non_ring_requests[source_project] = []
                    non_ring_requests[source_project].append(request_id)

        if len(non_ring_packages):
            print "Not in a ring:", ' '.join(sorted(non_ring_packages))
        else:
            return

        for source_project, requests in non_ring_requests.items():
            name = self.api.create_adi_project(None)

            for request in requests:
                if not self.api.rq_to_prj(request, name):
                    return False

            # Notify everybody about the changes
            self.api.update_status_comments(name, 'select')

    def perform(self, packages, move=False, by_dp=False, split=False):
        """
        Perform the list command
        """
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
