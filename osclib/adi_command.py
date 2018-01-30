import json
import urllib2

from colorama import Fore

from osc import oscerr
from osc.core import delete_project
from osc.core import show_package_meta
from osc import conf

from osclib.select_command import SelectCommand
from osclib.supersede_command import SupersedeCommand
from osclib.request_finder import RequestFinder
from osclib.request_splitter import RequestSplitter
from xml.etree import cElementTree as ET

class AdiCommand:
    def __init__(self, api):
        self.api = api
        self.config = conf.config[self.api.project]

    def check_adi_project(self, project):
        query_project = self.api.extract_staging_short(project)
        info = self.api.project_status(project, True)
        if len(info['selected_requests']):
            if len(info['building_repositories']):
                print query_project, Fore.MAGENTA + 'building'
                return
            if len(info['untracked_requests']):
                print query_project, Fore.YELLOW + 'untracked:', ', '.join(['{}[{}]'.format(
                    Fore.CYAN + req['package'] + Fore.RESET, req['number']) for req in info['untracked_requests']])
                return
            if len(info['obsolete_requests']):
                print query_project, Fore.YELLOW + 'obsolete:', ', '.join(['{}[{}]'.format(
                    Fore.CYAN + req['package'] + Fore.RESET, req['number']) for req in info['obsolete_requests']])
                return
            if len(info['broken_packages']):
                print query_project, Fore.RED + 'broken:', ', '.join([
                    Fore.CYAN + p['package'] + Fore.RESET for p in info['broken_packages']])
                return
            for review in info['missing_reviews']:
                print query_project, Fore.WHITE + 'review:', '{} for {}[{}]'.format(
                    Fore.YELLOW + review['by'] + Fore.RESET,
                    Fore.CYAN + review['package'] + Fore.RESET,
                    review['request'])
                return

        if self.api.is_user_member_of(self.api.user, self.api.cstaging_group):
            print query_project, Fore.GREEN + 'ready'
            packages = []
            for req in info['selected_requests']:
                print ' - {} [{}]'.format(Fore.CYAN + req['package'] + Fore.RESET, req['number'])
                self.api.rm_from_prj(project, request_id=req['number'], msg='ready to accept')
                packages.append(req['package'])
            self.api.accept_status_comment(project, packages)
            try:
                delete_project(self.api.apiurl, project, force=True)
            except urllib2.HTTPError as e:
                print(e)
                pass
        else:
            print query_project, Fore.GREEN + 'ready:', ', '.join(['{}[{}]'.format(
                Fore.CYAN + req['package'] + Fore.RESET, req['number']) for req in info['selected_requests']])

    def check_adi_projects(self):
        for p in self.api.get_adi_projects():
            self.check_adi_project(p)

    def create_new_adi(self, wanted_requests, by_dp=False, split=False):
        source_projects_expand = self.config.get('source_projects_expand', '').split()
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
            print(Fore.YELLOW + (group if group != '' else 'wanted') + Fore.RESET)

            name = None
            nonfree_repo_required = False
            for request in splitter.grouped[group]['requests']:
                request_id = int(request.get('id'))
                target_package = request.find('./action/target').get('package')
                target_project = request.find('./action/target').get('project')
                if self.api.cnonfree and self.api.cnonfree == target_project:
                    nonfree_repo_required = True
                line = '- {} {}{:<30}{}'.format(request_id, Fore.CYAN, target_package, Fore.RESET)

                message = self.api.ignore_format(request_id)
                if message:
                    print(line + '\n' + Fore.WHITE + message + Fore.RESET)
                    continue

                # Auto-superseding request in adi command
                stage_info, code = self.api.update_superseded_request(request)
                if stage_info:
                    print(line + ' ({})'.format(SupersedeCommand.CODE_MAP[code]))
                    continue

                # Only create staging projec the first time a non superseded
                # request is processed from a particular group.
                if name is None:
                    use_frozenlinks = group in source_projects_expand and not split
                    name = self.api.create_adi_project(None,
                            use_frozenlinks, group, nonfree_repo_required)

                if not self.api.rq_to_prj(request_id, name):
                    return False

                print(line + Fore.GREEN + ' (staged in {})'.format(name) + Fore.RESET)

            if name:
                # Notify everybody about the changes.
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
            if self.api.is_user_member_of(self.api.user, self.api.cstaging_group):
                self.create_new_adi((), by_dp=by_dp, split=split)
