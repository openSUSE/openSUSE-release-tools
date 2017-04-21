from colorama import Fore
from osc import oscerr
from osclib.request_splitter import RequestSplitter
from osclib.supersede_command import SupersedeCommand


class ListCommand:
    SOURCE_PROJECT_STRIP = [
        'SUSE:SLE-12:',
        'SUSE:SLE-12-',
        'openSUSE:Leap:'
        'openSUSE:',
        'home:',
    ]

    def __init__(self, api):
        self.api = api

    def perform(self, supersede=False):
        """
        Perform the list command
        """

        if supersede:
            SupersedeCommand(self.api).perform()

        requests = self.api.get_open_requests()
        requests_ignored = self.api.get_ignored_requests()

        splitter = RequestSplitter(self.api, requests, in_ring=True)
        splitter.filter_add('./action[@type="change_devel"]')
        change_devel_requests = splitter.filter_only()
        splitter.reset()

        splitter.filter_add('./action[not(@type="add_role" or @type="change_devel")]')
        splitter.group_by('./action/target/@devel_project')
        splitter.split()

        is_factory = self.api.project != 'openSUSE:Factory'
        for group in sorted(splitter.grouped.keys()):
            print Fore.YELLOW + group

            for request in splitter.grouped[group]['requests']:
                request_id = int(request.get('id'))
                action = request.find('action')
                target_package = action.find('target').get('package')
                ring = action.find('target').get('ring')
                if ring.startswith('0'):
                    ring = Fore.MAGENTA + ring + Fore.RESET
                if action.get('type') == 'delete':
                    ring += Fore.RED + ' (delete request)'

                line = 'sr#{}: {}{:<30}{} -> {:<12}'.format(request_id, Fore.CYAN, target_package, Fore.RESET, ring)

                if is_factory and action.find('source') != None:
                    source_project = action.find('source').get('project')
                    source_project = self.project_strip(source_project)
                    line += ' ({})'.format(source_project)

                if request_id in requests_ignored:
                    line += Fore.WHITE + '\n    ignored: ' + str(requests_ignored[request_id]) + Fore.RESET

                print ' ', line

        if len(splitter.other):
            non_ring_packages = []
            for request in splitter.other:
                non_ring_packages.append(request.find('./action/target').get('package'))
            print 'Not in a ring:', ' '.join(sorted(non_ring_packages))

        if len(change_devel_requests):
            print '\nChange devel requests:'
            for request in change_devel_requests:
                target_package = request.find('./action/target').get('package')
                url = self.api.makeurl(['request', 'show', request.get('id')])
                print('- request({}): {}'.format(target_package, url))

    def project_strip(self, source_project):
        home = source_project.startswith('home:')

        for prefix in self.SOURCE_PROJECT_STRIP:
            if source_project.startswith(prefix):
                source_project = source_project[len(prefix):]

        if home:
            source_project = '~' + source_project

        return source_project
