from collections import defaultdict
from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import get_request
from osc.core import http_GET

from osclib.comments import CommentAPI
from osclib.request_finder import RequestFinder
# from osclib.freeze_command import FreezeCommand


SELECT = 'select'
# SUPERSEDE = 'supersede'
MOVE = 'move'


class SelectCommand(object):

    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)
        self.pending_comments = defaultdict(lambda: defaultdict(list))

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

    def select_request(self, request, request_project, move, from_):
        supersede = self._supersede(request)

        if 'staging' not in request_project and not supersede:
            # Normal 'select' command
            print('Adding request "{}" to project "{}"'.format(request, self.target_project))

            # Add a new pending comment.
            user = get_request(self.api.apiurl, str(request)).get_creator()
            package = self._package(request)
            self.pending_comments[user][SELECT].append((package, request))

            return self.api.rq_to_prj(request, self.target_project)
        elif 'staging' in request_project and (move or supersede):
            # 'select' command becomes a 'move'
            fprj = None
            if from_:
                fprj = self.api.prj_from_letter(from_)
            else:
                # supersede = (new_rq, package, project)
                fprj = request_project['staging'] if not supersede else supersede[2]

            if supersede:
                print('"{} ({}) is superseded by {}'.format(request, supersede[1], supersede[0]))

            if fprj == self.target_project:
                print('"{}" is currently in "{}"'.format(request, self.target_project))
                return False

            print('Moving "{}" from "{}" to "{}"'.format(request, fprj, self.target_project))

            # Add a new pending comment.
            user = get_request(self.api.apiurl, str(request)).get_creator()
            package = self._package(request)
            self.pending_comments[user][MOVE].append((fprj, package, request))

            return self.api.move_between_project(fprj, request, self.target_project)
        elif 'staging' in request_project and not move:
            # Previously selected, but not explicit move
            msg = 'Request {} is already tracked in "{}".'
            msg = msg.format(request, request_project['staging'])
            if request_project['staging'] != self.target_project:
                msg += '\nUse --move modifier to move the request from "{}" to "{}"'
                msg = msg.format(request_project['staging'], self.target_project)
            print(msg)
            return True
        else:
            raise oscerr.WrongArgs('Arguments for select are not correct.')

    def perform(self, target_project, requests, move=False, from_=None):
        """
        Select package and move it accordingly by arguments
        :param target_project: project we want to target
        :param requests: requests we are working with
        :param move: wether to move the requests or not
        :param from_: location where from move the requests
        """

        # If the project is not frozen enough yet freeze it
        if not self.api.prj_frozen_enough(target_project):
            print('Freeze the prj first')
            # FreezeCommand(self.api).perform(target_project)
        self.target_project = target_project

        for request, request_project in RequestFinder.find_sr(requests, self.api.apiurl).items():
            if not self.select_request(request, request_project, move, from_):
                return False

        # Publish pending comments grouped by user and operation.
        for user in self.pending_comments:
            lines = []

            if SELECT in self.pending_comments[user]:
                lines.append('Packages tracked now in %s:\n' % self.target_project)
                for package, request in self.pending_comments[user][SELECT]:
                    lines.append('* %s [%s](%s)' % (package, request, '/request/show/' + str(request)))

            if MOVE in self.pending_comments[user]:
                if lines:
                    lines.append('\n')

                lines.append('Packages moved to %s:\n' % self.target_project)
                for from_project, package, request in self.pending_comments[user][MOVE]:
                    lines.append('*  %s [%s](%s) from %s' % (package, request, '/request/show/' + str(request),  from_project))
            lines.append('\nCC [at]%s' % user)

            msg = '\n'.join(lines)
            self.comment.add_comment(project_name=self.target_project, comment=msg)

        # now make sure we enable the prj if the prj contains any ringed package
        self.api.build_switch_staging_project(target_project)

        return True
