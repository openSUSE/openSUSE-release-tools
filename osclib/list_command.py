from osc import oscerr


class ListCommand:
    def __init__(self, api):
        self.api = api

    def perform(self, packages=None, supersede=False):
        """
        Perform the list command
        """

        if not packages:
            packages = []

        if supersede:
            if packages:
                self.api.dispatch_open_requests(packages)
            else:
                # First dispatch all possible requests
                self.api.dispatch_open_requests()

        # Print out the left overs
        requests = self.api.get_open_requests()
        requests_ignored = self.api.get_ignored_requests()

        non_ring_packages = []
        change_devel_requests = {}

        result = {}
        for request in requests:
            # Consolidate all data from request
            request_id = int(request.get('id'))
            action = request.findall('action')
            if not action:
                msg = 'Request {} has no action'.format(request_id)
                raise oscerr.WrongArgs(msg)
            # we care only about first action
            action = action[0]

            # Where are we targeting the package
            target_package = action.find('target').get('package')

            # ignore add_role requests
            if action.get('type') == 'add_role':
                continue

            # handle change_devel requests
            if action.get('type') == 'change_devel':
                change_devel_requests[target_package] = request_id
                continue

            # If the system have rings, we ask for the ring of the
            # package
            if self.api.crings:
                ring = self.api.ring_packages_for_links.get(target_package)
                if ring:
                    # cut off *:Rings: prefix
                    ring = ring[len(self.api.crings)+1:]
            else:
                ring = self.api.project

            # list all deletereq as in-ring
            if action.get('type') == 'delete':
                if ring:
                    ring = ring + " (delete request)"
                else:
                    ring = '(delete request)'

            # This condition is quite moot as we dispatched stuff
            # above anyway
            if ring:
                devel = self.api.get_devel_project("openSUSE:Factory", target_package)
                if devel is None:
                    devel = '00'
                result.setdefault(devel, []).append('sr#{}: {:<30} -> {:<12}'.format(request_id, target_package, ring))
                # show origin of request
                if self.api.project != "openSUSE:Factory" and action.find('source') != None:
                    source_prj = action.find('source').get('project')
                    if source_prj.startswith('SUSE:SLE-12:') \
                        or source_prj.startswith('SUSE:SLE-12-'):
                        source_prj = source_prj[len('SUSE:SLE-12:'):]
                    elif source_prj.startswith('openSUSE:'):
                        source_prj = source_prj[len('openSUSE:'):]
                        if source_prj.startswith('Leap:'):
                            source_prj = source_prj[len('Leap:'):]
                    elif source_prj.startswith('home:'):
                        source_prj = '~' + source_prj[len('home:'):]
                    result[devel][-1] += ' ({})'.format(source_prj)
                    if request_id in requests_ignored:
                        result[devel][-1] += '\nignored: ' + requests_ignored[request_id]
            else:
                non_ring_packages.append(target_package)

        for prj in sorted(result.keys()):
            print prj
            for line in result[prj]:
                print ' ', line.replace('\n', '\n    ')

        if len(non_ring_packages):
            print "Not in a ring:", ' '.join(sorted(non_ring_packages))
        if len(change_devel_requests):
            print "\nChange devel requests:"
            for package, requestid in change_devel_requests.items():
                print('Request({}): {}'.format(package, 'https://build.opensuse.org/request/show/'+str(requestid)))
