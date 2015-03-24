from osc import oscerr


class ListCommand:
    def __init__(self, api):
        self.api = api

    def perform(self):
        """
        Perform the list command
        """

        # First dispatch all possible requests
        self.api.dispatch_open_requests()

        # Print out the left overs
        requests = self.api.get_open_requests()

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

            # If the system have rings, we ask for the ring of the
            # package
            if self.api.crings:
                ring = self.api.ring_packages.get(target_package)
            else:
                ring = self.api.project

            # This condition is quite moot as we dispatched stuff
            # above anyway
            if ring:
                print('Request({}): {} -> {}'.format(request_id, target_package, ring))
