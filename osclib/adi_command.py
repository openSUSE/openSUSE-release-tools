import json
from urllib.error import HTTPError

from colorama import Fore

from osc import oscerr
from osc.core import get_request
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
        info = self.api.project_status(project, reload=True)

        if info.find('staged_requests/request') is not None:
            if info.find('building_repositories/repo') is not None:
                print(query_project + ' ' + Fore.MAGENTA + 'building')
                return
            if info.find('untracked_requests/request') is not None:
                print(query_project + ' ' + Fore.YELLOW + 'untracked: ' + ', '.join(['{}[{}]'.format(
                    Fore.CYAN + req.get('package') + Fore.RESET, req.get('id')) for req in info.findall('untracked_requests/request')]))
                return
            if info.find('obsolete_requests/request') is not None:
                print(query_project + ' ' + Fore.YELLOW + 'obsolete: ' + ', '.join(['{}[{}]'.format(
                    Fore.CYAN + req.get('package') + Fore.RESET, req.get('id')) for req in info.findall('obsolete_requests/request')]))
                return
            if info.find('broken_packages/package') is not None:
                print(query_project + ' ' + Fore.RED + 'broken: ' + ', '.join([
                    Fore.CYAN + p.get('package') + Fore.RESET for p in info.findall('broken_packages/package')]))
                return
            for review in info.findall('missing_reviews/review'):
                print(query_project + ' ' + Fore.WHITE + 'review: ' + '{} for {}[{}]'.format(
                    Fore.YELLOW + review.get('by', 'https://github.com/openSUSE/open-build-service/issues/8575') + Fore.RESET,
                    Fore.CYAN + review.get('package') + Fore.RESET,
                    review.get('id', 'https://github.com/openSUSE/open-build-service/issues/8575')))
                return
            for check in info.findall('missing_checks/check'):
                print(query_project + ' ' + Fore.MAGENTA + 'missing: {}'.format(check.get('name')))
                return
            for check in info.findall('checks/check'):
                state = check.find('state').text
                if state != 'success':
                    print(query_project + '{} {} check: {}'.format(Fore.MAGENTA, state, check.get('name')))
                    return

        overall_state = info.get('state')
        if overall_state != 'acceptable' and overall_state != 'empty':
            raise oscerr.WrongArgs('Missed some case')

        if self.api.is_user_member_of(self.api.user, self.api.cstaging_group):
            print(query_project + ' ' + Fore.GREEN + 'ready')
            packages = []
            for req in info.findall('staged_requests/request'):
                msg = 'ready to accept'
                print(' - {} [{}]'.format(Fore.CYAN + req.get('package') + Fore.RESET, req.get('id')))
                self.api.rm_from_prj(project, request_id=req.get('id'), msg=msg)
                self.api.do_change_review_state(req.get('id'), 'accepted', by_group=self.api.cstaging_group, message=msg)
                packages.append(req.get('package'))
            self.api.accept_status_comment(project, packages)
            try:
                delete_project(self.api.apiurl, project, force=True)
            except HTTPError as e:
                print(e)
                pass
        else:
            ready=[]
            for req in info.findall('staged_requests/request'):
                ready.append('{}[{}]'.format(Fore.CYAN + req.get('package') + Fore.RESET, req.get('id')))
            if len(ready):
                print(query_project, Fore.GREEN + 'ready:', ', '.join(ready))

    def check_adi_projects(self):
        for p in self.api.get_adi_projects():
            self.check_adi_project(p)

    def create_new_adi(self, wanted_requests, by_dp=False, split=False):
        source_projects_expand = self.config.get('source_projects_expand', '').split()
        # if we don't call it, there is no invalidate function added
        requests = self.api.get_open_requests()
        if len(wanted_requests):
            requests = []
            rf = RequestFinder(self.api)
            for p in wanted_requests:
                requests.append(rf.load_request(p))

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

            if not split:
                splitter.group_by('./action/target/@nonfree')
        splitter.split()

        for group in sorted(splitter.grouped.keys()):
            print(Fore.YELLOW + (group if group != '' else 'wanted') + Fore.RESET)

            name = None
            for request in splitter.grouped[group]['requests']:
                request_id = int(request.get('id'))
                target = request.find('./action/target')
                target_package = target.get('package')
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
                    nonfree = bool(target.get('nonfree'))
                    name = self.api.create_adi_project(None,
                            use_frozenlinks, group, nonfree)

                if not self.api.rq_to_prj(request_id, name):
                    return False

                print(line + Fore.GREEN + ' (staged in {})'.format(name) + Fore.RESET)

    def perform(self, packages, move=False, by_dp=False, split=False):
        """
        Perform the list command
        """
        if len(packages):
            requests = set()
            if move:
                items = RequestFinder.find_staged_sr(packages, self.api).items()
                print(items)
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
