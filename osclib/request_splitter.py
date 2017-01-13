from lxml import etree as ET

class RequestSplitter(object):
    def __init__(self, api, requests, in_ring):
        self.api = api
        self.requests = requests
        self.in_ring = in_ring
        self.requests_ignored = self.api.get_ignored_requests()
        self.reset()

    def reset(self):
        self.filters = []
        self.groups = []

        # after split()
        self.filtered = []
        self.other = []
        self.grouped = {}

    def filter_add(self, xpath):
        self.filters.append(ET.XPath(xpath))

    def filter_add_requests(self, requests):
        requests = ' ' + ' '.join(requests) + ' '
        self.filter_add('contains("{requests}", concat(" ", @id, " ")) or '
                        'contains("{requests}", concat(" ", ./action/target/@package, " "))'
                        .format(requests=requests))

    def group_by(self, xpath):
        self.groups.append(ET.XPath(xpath))

    def filter_only(self):
        ret = []
        for request in self.requests:
            target_package = request.find('./action/target').get('package')
            self.suppliment(request, target_package)
            if self.filter_check(request):
                ret.append(request)
        return ret

    def split(self):
        for request in self.requests:
            target_package = request.find('./action/target').get('package')
            self.suppliment(request, target_package)

            if not self.filter_check(request):
                continue

            if self.in_ring != (not self.api.ring_packages.get(target_package)):
                # Request is of desired ring type.
                key = self.group_key_build(request)
                if key not in self.grouped:
                    self.grouped[key] = {
                        'bootstrap_required': False,
                        'requests': [],
                    }

                self.grouped[key]['requests'].append(request)

                ring = request.find('./action/target').get('ring')
                if ring and ring.startswith('0'):
                    self.grouped[key]['bootstrap_required'] = True
            else:
                self.other.append(request)

    def suppliment(self, request, target_package):
        """ Provide additional information for grouping """
        devel = self.devel_project_get(request, target_package)
        if devel:
            request.find('./action/source').set('devel_project', devel)

        ring = self.ring_get(target_package)
        if ring:
            request.find('./action/target').set('ring', ring)

        request_id = int(request.get('id'))
        if request_id in self.requests_ignored:
            request.set('ignored', self.requests_ignored[request_id])
        else:
            request.set('ignored', 'false')

    def ring_get(self, target_package):
        if self.api.crings:
            ring = self.api.ring_packages_for_links.get(target_package)
            if ring:
                # Cut off *:Rings: prefix.
                return ring[len(self.api.crings)+1:]
        return None

    def devel_project_get(self, request, target_project):
        # Preserve logic from adi and note that not Leap development friendly.
        source = request.find('./action/source')
        devel = self.api.get_devel_project(source.get('project'), source.get('package'))
        if devel is None and self.api.project.startswith('openSUSE:'):
            devel = self.api.get_devel_project('openSUSE:Factory', target_project)
        return devel

    def filter_check(self, request):
        for xpath in self.filters:
            if not xpath(request):
                return False
        return True

    def group_key_build(self, request):
        if len(self.groups) == 0:
            return 'all'

        key = []
        for xpath in self.groups:
            element = xpath(request)
            if element:
                key.append(element[0])
        return '__'.join(key)
