from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import http_GET

from osclib.request_finder import RequestFinder


class SelectCommand(object):

    def __init__(self, api):
        self.api = api

    def _package(self, request):
        """Get the package name from the submit request XML."""
        f = http_GET(self.api.makeurl(['request', str(request)]))
        root = ET.parse(f).getroot()
        package = str(root.find('action').find('target').attrib['package'])
        return package

    def _is_supersede(self, request):
        """
        Check if the request supersede a different request from a
        staging project.
        """
        package = self._package(request)

        for staging in self.api.get_staging_projects():
            for rq in self.api.get_prj_pseudometa(staging)['requests']:
                if rq['id'] != request and rq['package'] == package:
                    return (rq['id'], package, staging)

    def select_request(self, rq, rq_prj, move, from_):
        supersede = self._is_supersede(rq)

        if 'staging' not in rq_prj and not supersede:
            # Normal 'select' command
            return self.api.rq_to_prj(rq, self.tprj)
        elif 'staging' in rq_prj and (move or supersede):
            # 'select' command becomes a 'move'
            fprj = None
            if from_:
                fprj = self.api.prj_from_letter(from_)
            else:
                # supersede = (new_rq, package, project)
                fprj = rq_prj['staging'] if not supersede else supersede[2]
            if supersede:
                print('"{} ({}) is superseded by {}'.format(rq, supersede[1], supersede[0]))
            print('Moving "{}" from "{}" to "{}"'.format(rq, fprj, self.tprj))
            return self.api.move_between_project(fprj, rq, self.tprj)
        elif 'staging' in rq_prj and not move:
            # Previously selected, but not explicit move
            msg = 'Request {} is actually in "{}".\n'
            msg = msg.format(rq, rq_prj['staging'])
            if rq_prj['staging'] != self.tprj:
                msg += 'Use --move modifier to move the request from "{}" to "{}"'
                msg = msg.format(rq_prj['staging'], self.tprj)
            print(msg)
            return False
        else:
            raise oscerr.WrongArgs('Arguments for select are not correct.')

    def perform(self, tprj, requests, move=False, from_=None):
        if not self.api.prj_frozen_enough(tprj):
            print('Freeze the prj first')
            return False
        self.tprj = tprj

        for rq, rq_prj in RequestFinder.find_sr(requests, self.api.apiurl).items():
            if not self.select_request(rq, rq_prj, move, from_):
                return False

        # now make sure we enable the prj
        self.api.build_switch_prj(tprj, 'enable')
        return True
