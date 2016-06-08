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

            # handle change_devel requests
            if action.get('type') == 'change_devel':
                change_devel_requests[target_package] = request_id
                continue

            # If the system have rings, we ask for the ring of the
            # package
            if self.api.crings:
                ring = self.api.ring_packages.get(target_package)
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
                result.setdefault(devel, []).append('sr#{}: {:<30} -> {}'.format(request_id, target_package, ring))
            else:
                non_ring_packages.append(target_package)

        for prj in sorted(result.keys()):
            print prj
            for line in result[prj]:
                print ' ', line

        if len(non_ring_packages):
            print "Not in a ring:", ' '.join(sorted(non_ring_packages))
        if len(change_devel_requests):
            print "\nChange devel requests:"
            for package, requestid in change_devel_requests.items():
                print('Request({}): {}'.format(package, 'https://build.opensuse.org/request/show/'+str(requestid)))
