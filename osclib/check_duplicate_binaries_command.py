from __future__ import print_function
from osclib.core import duplicated_binaries_in_repo
import yaml

class CheckDuplicateBinariesCommand(object):
    def __init__(self, api):
        self.api = api

    def perform(self, save=False):
        duplicates = duplicated_binaries_in_repo(self.api.apiurl, self.api.project, 'standard')

        current = yaml.dump(duplicates, default_flow_style=False)
        if save:
            self.api.pseudometa_file_ensure('duplicate_binaries', current)
        else:
            print(current)
