from osc import oscerr

from osclib.select_command import SelectCommand

class AdiCommand:
    def __init__(self, api):
        self.api = api

    def perform(self):
        """
        Perform the list command
        """

        # Print out the left overs
        requests = self.api.get_open_requests()

        non_ring_packages = []
        non_ring_requests = []
        
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

            if not self.api.ring_packages.get(target_package):
                non_ring_packages.append(target_package)
                non_ring_requests.append(request_id)
                
        if len(non_ring_packages):
            print "Not in a ring:", ' '.join(sorted(non_ring_packages))
            
        name = self.api.create_adi_project(None)

        sc = SelectCommand(self.api, name)
        
        for request in non_ring_requests:
            if not self.api.rq_to_prj(request, name):
                return False

        # Notify everybody about the changes
        self.api.update_status_comments(name, 'select')
