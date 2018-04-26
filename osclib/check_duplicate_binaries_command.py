from __future__ import print_function
from osclib.core import package_binary_list
from osclib.core import target_archs
import yaml


class CheckDuplicateBinariesCommand(object):
    def __init__(self, api):
        self.api = api
        # some packages create packages with the same name but
        # different architecture than built for.
        self.ignore_extra_archs = {
            'i586': {
                'glibc:i686': ('i686',)
            },
            'x86_64': {
                'syslinux': ('s390x', 'ppc64le',)
            }
        }

    def perform(self, save=False):
        duplicates = {}
        for arch in sorted(target_archs(self.api.apiurl, self.api.project), reverse=True):
            package_binaries, _ = package_binary_list(
                self.api.apiurl, self.api.project, 'standard', arch,
                strip_multibuild=False, exclude_src_debug=True)
            binaries = {}
            for pb in package_binaries:
                if arch in self.ignore_extra_archs \
                    and pb.package in self.ignore_extra_archs[arch] \
                    and pb.arch in self.ignore_extra_archs[arch][pb.package]:
                    continue

                binaries.setdefault(arch, {})

                if pb.name in binaries[arch]:
                    duplicates.setdefault(arch, {})
                    duplicates[arch].setdefault(pb.name, set())
                    duplicates[arch][pb.name].add(pb.package)
                    duplicates[arch][pb.name].add(binaries[arch][pb.name])

                    continue

                binaries[arch][pb.name] = pb.package

        # convert sets to lists for readable yaml
        for arch in duplicates.keys():
            for name in duplicates[arch].keys():
                duplicates[arch][name] = list(duplicates[arch][name])

        current = yaml.dump(duplicates, default_flow_style=False)
        if save:
            args = ['{}:Staging'.format(self.api.project), 'dashboard', 'duplicate_binaries']
            previous = self.api.load_file_content(*args)
            if current != previous:
                args.append(current)
                self.api.save_file_content(*args)
        else:
            print(current)

