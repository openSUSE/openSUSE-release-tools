import osc
from osc import cmdln
from osc.core import *

class ListCommand:
    def __init__(self, api):
        self.api = api

    def perform(self):
        self.packages_staged = dict()
        for prj in self.api.get_staging_projects():
            meta = self.api.get_prj_pseudometa(prj)
            for req in meta['requests']:
                self.packages_staged[req['package']] = (prj[-1], req['id'])

        where = "@by_group='factory-staging'+and+@state='new'"

        url = makeurl(self.api.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        self.supersedes = dict()
        for rq in root.findall('request'):
            self.one_request(rq)
        for letter, reqs in self.supersedes.items():
            print("osc staging select {} {}".format(letter, ' '.join(reqs)))

    def one_request(self, rq):
        id = int(rq.get('id'))
        act_id = 0
        actions = rq.findall('action')
        act = actions[0]

        tprj = act.find('target').get('project')
        tpkg = act.find('target').get('package')

        stage_info = self.packages_staged.get(tpkg, ('', 0))
        if stage_info[1] != 0 and int(stage_info[1]) != id:
            reqs = self.supersedes.get(stage_info[0], [])
            reqs.append(str(id))
	    self.supersedes[stage_info[0]] = reqs
            return

        ring = self.api.ring_packages.get(tpkg)
        if ring:
            print("Request(%d): %s -> %s" % (id, tpkg, ring))
            return
            
        # no ring, no group -> ok
        self.api.change_review_state(id, 'accepted', by_group='factory-staging', message='ok')
