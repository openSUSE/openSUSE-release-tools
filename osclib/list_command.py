from xml.etree import cElementTree as ET

from osc.core import makeurl
from osc.core import http_GET


class ListCommand:
    def __init__(self, api):
        self.api = api

    def perform(self):
        """
        Perform the list command
        """
        self.packages_staged = dict()
        for prj in self.api.get_staging_projects():
            meta = self.api.get_prj_pseudometa(prj)
            for req in meta['requests']:
                self.packages_staged[req['package']] = {'prj': prj, 'rq_id': req['id'] }

        where = "@by_group='factory-staging'+and+@state='new'"

        url = makeurl(self.api.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            self.one_request(rq)

    def one_request(self, request):
        """
        Process one request potentially to be listed
        :param request: request to process
        """
        rq_id = int(request.get('id'))
        actions = request.findall('action')
        act = actions[0]

        tpkg = act.find('target').get('package')

        # Replace superseded
        stage_info = self.packages_staged.get(tpkg, {'prj': '', 'rq_id': 0})
        if stage_info['rq_id'] != 0 and int(stage_info['rq_id']) != rq_id:
            # Remove the old request
            self.api.rm_from_prj(stage_info['prj'], request_id=stage_info['rq_id'],
                                 review='declined', msg='Replaced by newer request')
            # Add the new one that should be replacing it
            self.api.rq_to_prj(rq_id, stage_info['prj'])
            # Update local data structure
            self.packages_staged[tpkg]['rq_id'] = rq_id
            return

        ring = self.api.ring_packages.get(tpkg)
        if ring:
            print("Request(%d): %s -> %s" % (rq_id, tpkg, ring))
            return

        # no ring, no group -> ok
        self.api.change_review_state(rq_id, 'accepted', by_group='factory-staging', message='ok')
