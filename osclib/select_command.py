from __future__ import print_function

from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import http_GET

from osclib.request_finder import RequestFinder
# from osclib.freeze_command import FreezeCommand


SELECT = 'select'
# SUPERSEDE = 'supersede'
MOVE = 'move'


class SelectCommand(object):

    def __init__(self, api, target_project):
        self.api = api
        self.affected_projects = set()
        self.target_project = target_project

    def _package(self, request):
        """
        Get the package name from the submit request XML.
        :param request: request we check for
        """
        f = http_GET(self.api.makeurl(['request', str(request)]))
        root = ET.parse(f).getroot()
        package = str(root.find('action').find('target').attrib['package'])
        return package

    def _supersede(self, request):
        """
        Check if the request supersede a different request from a
        staging project.

        SRA supersede SRB when (1) SRA ID > SRB ID and (2) the changes
        in SRB are in SRA. The second condition is difficult to
        assure, but the way that we implement RequestFinder can
        address some corner cases that make the first condition
        enough.

        :param request: request we check for
        """
        package = self._package(request)

        candidates = []   # Store candidates to be supersede by 'request'
        for staging in self.api.get_staging_projects():
            # requests for the same project are fine
            if staging == self.target_project:
                continue
            for rq in self.api.get_prj_pseudometa(staging)['requests']:
                if int(rq['id']) < int(request) and rq['package'] == package:
                    candidates.append((rq['id'], package, staging))

        assert len(candidates) <= 1, 'There are more thant one candidate to supersede {} ({}): {}'.format(request, package, candidates)

        return candidates[0] if candidates else None

    def select_request(self, request, move, filter_from):
        supersede = self._supersede(request)

        staged_requests = {
            self.api.packages_staged[package]['rq_id']: package for package in self.api.packages_staged
        }

        if request not in staged_requests and not supersede:
            # Normal 'select' command
            print('Adding request "{}" to project "{}"'.format(request, self.target_project))

            return self.api.rq_to_prj(request, self.target_project)
        elif request in staged_requests and (move or supersede):
            # 'select' command becomes a 'move'
            # supersede = (new_rq, package, project)
            fprj = self.api.packages_staged[staged_requests[request]]['prj'] if not supersede else supersede[2]
            if filter_from and filter_from != fprj:
                print('Ignoring "{}" in "{}" since not in "{}"'.format(request, fprj, filter_from))
                return True

            if supersede:
                print('"{} ({}) is superseded by {}'.format(request, supersede[1], supersede[0]))

            if fprj == self.target_project:
                print('"{}" is currently in "{}"'.format(request, self.target_project))
                return False

            print('Moving "{}" from "{}" to "{}"'.format(request, fprj, self.target_project))

            # Store the source project, we also need to write a comment there
            self.affected_projects.add(fprj)

            return self.api.move_between_project(fprj, request, self.target_project)
        elif request in staged_requests and not move:
            # Previously selected, but not explicit move
            fprj = self.api.packages_staged[staged_requests[request]]['prj']
            msg = 'Request {} is already tracked in "{}".'
            msg = msg.format(request, fprj)
            if fprj != self.target_project:
                msg += '\nUse --move modifier to move the request from "{}" to "{}"'
                msg = msg.format(fprj, self.target_project)
            print(msg)
            return True
        elif supersede:
            print('"{} ({}) supersedes {}'.format(request, supersede[1], supersede[0]))
        else:
            raise oscerr.WrongArgs('Arguments for select are not correct.')

    def perform(self, requests, move=False,
                filter_from=None, no_freeze=False):
        """
        Select package and move it accordingly by arguments
        :param target_project: project we want to target
        :param requests: requests we are working with
        :param move: wether to move the requests or not
        :param filter_from: filter request list to only those from a specific staging
        """

        if self.api.is_adi_project(self.target_project):
            no_freeze = True

        # If the project is not frozen enough yet freeze it
        if not (no_freeze or self.api.prj_frozen_enough(self.target_project)):
            print('Freeze the prj first')
            return False
            # FreezeCommand(self.api).perform(self.target_project)

        # picks new candidate requests only if it's not to move requests
        # ie. the review state of staging-project must be new if newcand is True
        newcand = not move

        requests = RequestFinder.find_sr(requests, self.api, newcand)
        requests_count = len(requests)
        for index, request in enumerate(requests, start=1):
            print('({}/{}) '.format(index, requests_count), end='')
            if not self.select_request(request, move, filter_from):
                return False

        # Notify everybody about the changes
        self.api.update_status_or_deactivate(self.target_project, 'select')
        for fprj in self.affected_projects:
            self.api.update_status_or_deactivate(fprj, 'select')

        return True
