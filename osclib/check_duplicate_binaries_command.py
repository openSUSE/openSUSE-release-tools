from __future__ import print_function
from osc.core import get_binarylist
from osclib.core import package_list
from osclib.core import target_archs
import re
import yaml


class CheckDuplicateBinariesCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, save=False):
        duplicates = {}
        for arch in sorted(target_archs(self.api.apiurl, self.api.project), reverse=True):
            print('arch {}'.format(arch))

            binaries = {}
            duplicates[arch] = {}
            for package in package_list(self.api.apiurl, self.api.project):
                for binary in get_binarylist(self.api.apiurl, self.api.project,
                                             'standard', arch, package):
                    # StagingAPI.fileinfo_ext(), but requires lots of calls.
                    match = re.match(r'(.*)-([^-]+)-([^-]+)\.([^-\.]+)\.rpm', binary)
                    if not match or match.group(4) == 'src': continue

                    name = match.group(1)
                    if name in binaries:
                        print('DUPLICATE', package, binaries[name], name)

                        if name not in duplicates[arch]:
                            # Only add the first package found once.
                            duplicates[arch][name] = [binaries[name]]

                        duplicates[arch][name].append(package)
                        continue

                    binaries[name] = package
                    print(package, name)

        if save:
            args = ['{}:Staging'.format(self.api.project), 'dashboard', 'duplicate_binaries']
            previous = self.api.load_file_content(*args)
            current = yaml.dump(duplicates, default_flow_style=False)
            if current != previous:
                args.append(current)
                self.api.save_file_content(*args)
