import hashlib
from lxml import etree as ET

class RequestSplitter(object):
    def __init__(self, api, requests, in_ring):
        self.api = api
        self.requests = requests
        self.in_ring = in_ring
        self.mergeable_build_percent = 80

        self.requests_ignored = self.api.get_ignored_requests()

        self.reset()
        # after propose_assignment()
        self.proposal = {}

    def reset(self):
        self.strategy = None
        self.filters = []
        self.groups = []

        # after split()
        self.filtered = []
        self.other = []
        self.grouped = {}

    def strategy_set(self, name, **kwargs):
        self.reset()

        class_name = 'Strategy{}'.format(name.lower().title())
        cls = globals()[class_name]
        self.strategy = cls(**kwargs)
        self.strategy.apply(self)

    def strategy_from_splitter_info(self, splitter_info):
        strategy = splitter_info['strategy']
        if 'args' in strategy:
            self.strategy_set(strategy['name'], **strategy['args'])
        else:
            self.strategy_set(strategy['name'])

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

            ring = request.find('./action/target').get('ring')
            if self.in_ring != (not ring):
                # Request is of desired ring type.
                key = self.group_key_build(request)
                if key not in self.grouped:
                    self.grouped[key] = {
                        'bootstrap_required': False,
                        'requests': [],
                    }

                self.grouped[key]['requests'].append(request)

                if ring and ring.startswith('0'):
                    self.grouped[key]['bootstrap_required'] = True
            else:
                self.other.append(request)

    def suppliment(self, request):
        """ Provide additional information for grouping """
        if request.get('ignored'):
            # Only supliment once.
            return

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
            request.set('ignored', str(self.requests_ignored[request_id]))
        else:
            request.set('ignored', 'false')

    def ring_get(self, target_package):
        if self.api.crings:
            ring = self.api.ring_packages_for_links.get(target_package)
            if ring:
                # Cut off *:Rings: prefix.
                return ring[len(self.api.crings)+1:]
        else:
            # Projects not using rings handle all requests as ring requests.
            return self.api.project
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
        if len(key) == 0:
            return '00'
        return '__'.join(key)

    def is_staging_bootstrapped(self, project):
        if self.api.rings:
            # Determine if staging is bootstrapped.
            meta = self.api.get_prj_meta(project)
            xpath = 'link[@project="{}"]'.format(self.api.rings[0])
            return meta.find(xpath) is not None

        return False

    def is_staging_mergeable(self, status, pseudometa):
        # Mergeable if building and not too far along.
        return (len(pseudometa['requests']) > 0 and
                'splitter_info' in pseudometa and
                status['overall_state'] == 'building' and
                self.api.project_status_build_percent(status) <= self.mergeable_build_percent)

    def staging_status_load(self, project):
        status = self.api.project_status(project)
        return status, self.api.load_prj_pseudometa(status['description'])

    def is_staging_considerable(self, project, pseudometa):
        return (len(pseudometa['requests']) == 0 and
                self.api.prj_frozen_enough(project))

    def stagings_load(self, stagings):
        self.stagings = {}
        self.stagings_considerable = []
        self.stagings_mergeable = []
        self.stagings_mergeable_none = []

        # Use specified list of stagings, otherwise only empty, letter stagings.
        if len(stagings) == 0:
            stagings = self.api.get_staging_projects_short()

        for staging in stagings:
            project = self.api.prj_from_short(staging)
            status, pseudometa = self.staging_status_load(project)

            # Store information about staging.
            self.stagings[staging] = {
                'project': project,
                'bootstrapped': self.is_staging_bootstrapped(project),
                'status': status,
                'pseudometa': pseudometa,
            }

            # Decide if staging of interested.
            if self.is_staging_mergeable(status, pseudometa):
                if pseudometa['splitter_info']['strategy']['name'] == 'none':
                    self.stagings_mergeable_none.append(staging)
                else:
                    self.stagings_mergeable.append(staging)
            elif self.is_staging_considerable(project, pseudometa):
                self.stagings_considerable.append(staging)

        # Allow both considered and remaining to be accessible after proposal.
        self.stagings_available = list(self.stagings_considerable)

        return (len(self.stagings_considerable) +
                len(self.stagings_mergeable) +
                len(self.stagings_mergeable_none))

    def propose_assignment(self):
        # Attempt to assign groups that have bootstrap_required first.
        for group in sorted(self.grouped.keys()):
            if self.grouped[group]['bootstrap_required']:
                staging = self.propose_staging(choose_bootstrapped=True)
                if staging:
                    self.requests_assign(group, staging)

        # Assign groups that do not have bootstrap_required and fallback to a
        # bootstrapped staging if no non-bootstrapped stagings available.
        for group in sorted(self.grouped.keys()):
            if not self.grouped[group]['bootstrap_required']:
                staging = self.propose_staging(choose_bootstrapped=False)
                if staging:
                    self.requests_assign(group, staging)
                    continue

                staging = self.propose_staging(choose_bootstrapped=True)
                if staging:
                    self.requests_assign(group, staging)

    def requests_assign(self, group, staging, merge=False):
        # Arbitrary, but descriptive group key for proposal.
        key = '{}#{}@{}'.format(len(self.proposal), self.strategy.key, group)
        self.proposal[key] = {
            'bootstrap_required': self.grouped[group]['bootstrap_required'],
            'group': group,
            'requests': {},
            'staging': staging,
            'strategy': self.strategy.info(),
        }
        if merge:
            self.proposal[key]['merge'] = True

        # Covert request nodes to simple proposal form.
        for request in self.grouped[group]['requests']:
            self.proposal[key]['requests'][int(request.get('id'))] = request.find('action/target').get('package')
            self.requests.remove(request)

        return key

    def propose_staging(self, choose_bootstrapped):
        found = False
        for staging in sorted(self.stagings_available):
            if choose_bootstrapped == self.stagings[staging]['bootstrapped']:
                found = True
                break

        if found:
            self.stagings_available.remove(staging)
            return staging

        return None

    def strategies_try(self):
        strategies = (
            'special',
            'devel',
        )

        map(self.strategy_try, strategies)

    def strategy_try(self, name):
        self.strategy_set(name)
        self.split()

        groups = self.strategy.desirable(self)
        if len(groups) == 0:
            return
        self.filter_grouped(groups)

        self.propose_assignment()

    def strategy_do(self, name, **kwargs):
        self.strategy_set(name, **kwargs)
        self.split()
        self.propose_assignment()

    def filter_grouped(self, groups):
        for group in sorted(self.grouped.keys()):
            if group not in groups:
                del self.grouped[group]

    def merge_staging(self, staging, pseudometa):
        splitter_info = pseudometa['splitter_info']
        self.strategy_from_splitter_info(splitter_info)

        if not self.stagings[staging]['bootstrapped']:
            # If when the strategy was first run the resulting staging was not
            # bootstrapped then ensure no bootstrapped packages are included.
            self.filter_add('./action/target[not(starts-with(@ring, "0"))]')

        self.split()

        group = splitter_info['group']
        if group in self.grouped:
            key = self.requests_assign(group, staging, merge=True)

    def merge(self, strategy_none=False):
        stagings = self.stagings_mergeable_none if strategy_none else self.stagings_mergeable
        for staging in sorted(stagings):
            self.merge_staging(staging, self.stagings[staging]['pseudometa'])


class Strategy(object):
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.name = self.__class__.__name__[8:].lower()
        self.key = self.name
        if kwargs:
            self.key += '_' + hashlib.sha1(str(kwargs)).hexdigest()[:7]

    def info(self):
        info = {'name': self.name}
        if self.kwargs:
            info['args'] = self.kwargs
        return info

class StrategyNone(Strategy):
    def apply(self, splitter):
        splitter.filter_add('./action[not(@type="add_role" or @type="change_devel")]')
        splitter.filter_add('@ignored="false"')

class StrategyRequests(Strategy):
    def apply(self, splitter):
        splitter.filter_add_requests(self.kwargs['requests'])

class StrategyCustom(StrategyNone):
    def apply(self, splitter):
        if 'filters' not in self.kwargs:
            super(StrategyCustom, self).apply(splitter)
        else:
            map(splitter.filter_add, self.kwargs['filters'])

        if 'groups' in self.kwargs:
            map(splitter.group_by, self.kwargs['groups'])

class StrategyDevel(StrategyNone):
    GROUP_MIN = 7

    def apply(self, splitter):
        super(StrategyDevel, self).apply(splitter)
        splitter.group_by('./action/target/@devel_project')

    def desirable(self, splitter):
        groups = []
        for group, info in sorted(splitter.grouped.items()):
            if len(info['requests']) >= self.GROUP_MIN:
                groups.append(group)
        return groups

class StrategySpecial(StrategyNone):
    PACKAGES = [
        'boost',
        'gcc',
        'gcc6',
        'gcc7',
        'glibc',
        'kernel-source',
        'python2',
        'python3',
        'util-linux',
    ]

    def apply(self, splitter):
        super(StrategySpecial, self).apply(splitter)
        splitter.filter_add_requests(self.PACKAGES)
        splitter.group_by('./action/target/@package')

    def desirable(self, splitter):
        return splitter.grouped.keys()
