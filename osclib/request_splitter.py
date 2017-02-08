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
        # after propose_assignment()
        self.proposal = {}

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
            self.suppliment(request)
            if self.filter_check(request):
                ret.append(request)
        return ret

    def split(self):
        for request in self.requests:
            self.suppliment(request)

            if not self.filter_check(request):
                continue

            target_package = request.find('./action/target').get('package')
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

    def suppliment(self, request):
        """ Provide additional information for grouping """
        target = request.find('./action/target')
        target_project = target.get('project')
        target_package = target.get('package')
        devel = self.devel_project_get(target_project, target_package)
        if devel:
            target.set('devel_project', devel)

        ring = self.ring_get(target_package)
        if ring:
            target.set('ring', ring)
        elif request.find('./action').get('type') == 'delete':
            # Delete requests should always be considered in a ring.
            target.set('ring', 'delete')

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

    def devel_project_get(self, target_project, target_package):
        devel = self.api.get_devel_project(target_project, target_package)
        if devel is None and self.api.project.startswith('openSUSE:'):
            devel = self.api.get_devel_project('openSUSE:Factory', target_package)
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

    def propose_stagings_load(self, stagings):
        self.stagings_considerable = {}

        if self.api.rings:
            xpath = 'link[@project="{}"]'.format(self.api.rings[0])

        # Use specified list of stagings, otherwise only empty, letter stagings.
        if len(stagings) == 0:
            stagings = self.api.get_staging_projects_short()
            filter_skip = False
        else:
            filter_skip = True

        for staging in stagings:
            project = self.api.prj_from_short(staging)

            if not filter_skip:
                if len(staging) > 1:
                    continue

                # TODO Allow stagings that have not finished building by threshold.
                if len(self.api.get_prj_pseudometa(project)['requests']) > 0:
                    continue

            if self.api.rings:
                # Determine if staging is bootstrapped.
                meta = self.api.get_prj_meta(project)
                self.stagings_considerable[staging] = True if meta.find(xpath) is not None else False
            else:
                self.stagings_considerable[staging] = False

        # Allow both considered and remaining to be accessible after proposal.
        self.stagings_available = self.stagings_considerable.copy()

    def propose_assignment(self, stagings):
        # Determine available stagings and make working copy.
        self.propose_stagings_load(stagings)

        if len(self.grouped) > len(self.stagings_available):
            return 'more groups than available stagings'

        # Cycle through all groups and initialize proposal and attempt to assign
        # groups that have bootstrap_required.
        for group in sorted(self.grouped.keys()):
            self.proposal[group] = {
                'bootstrap_required': self.grouped[group]['bootstrap_required'],
                'requests': {},
            }

            # Covert request nodes to simple proposal form.
            for request in self.grouped[group]['requests']:
                self.proposal[group]['requests'][int(request.get('id'))] = request.find('action/target').get('package')

            if self.grouped[group]['bootstrap_required']:
                self.proposal[group]['staging'] = self.propose_staging(True)
                if not self.proposal[group]['staging']:
                    return 'unable to find enough available bootstrapped stagings'

        # Assign groups that do not have bootstrap_required and fallback to a
        # bootstrapped staging if no non-bootstrapped stagings available.
        for group in sorted(self.grouped.keys()):
            if not self.grouped[group]['bootstrap_required']:
                self.proposal[group]['staging'] = self.propose_staging(False)
                if self.proposal[group]['staging']:
                    continue

                self.proposal[group]['staging'] = self.propose_staging(True)
                if not self.proposal[group]['staging']:
                    return 'unable to find enough available stagings'

        return True

    def propose_staging(self, choose_bootstrapped):
        found = False
        for staging, bootstrapped in sorted(self.stagings_available.items()):
            if choose_bootstrapped == bootstrapped:
                found = True
                break

        if found:
            del self.stagings_available[staging]
            return staging

        return None
