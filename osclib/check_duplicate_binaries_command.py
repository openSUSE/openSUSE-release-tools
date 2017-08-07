from __future__ import print_function
from osc.core import http_GET
from osclib.core import package_list
from osclib.core import target_archs
from xml.etree import cElementTree as ET
from pprint import pprint
import re
import urllib2
import yaml


class CheckDuplicateBinariesCommand(object):
    def __init__(self, api):
        self.api = api
        # some packages create packages with the same name but
        # different architecture than built for.
        self.ignore_extra_archs = {
            'i586': {
                'glibc.i686': ('i686',)
            },
            'x86_64': {
                'syslinux': ('s390x', 'ppc64le',)
            }
        }

    def perform(self, save=False):
        duplicates = {}
        for arch in sorted(target_archs(self.api.apiurl, self.api.project), reverse=True):
            url = self.api.makeurl(['build', self.api.project, 'standard', arch], { 'view': 'binaryversions' })
            data = http_GET(url)
            root = ET.parse(data).getroot()

            binaries = {}
            for packagenode in root.findall('.//binaryversionlist'):
                package = packagenode.get('package')
                for binarynode in packagenode.findall('binary'):
                    binary = binarynode.get('name')
                    # StagingAPI.fileinfo_ext(), but requires lots of calls.
                    match = re.match(r'(.*)-([^-]+)-([^-]+)\.([^-\.]+)\.rpm', binary)
                    if not match:
                        continue
                    parch = match.group(4)
                    if parch in ('src', 'nosrc'):
                        continue

                    name = match.group(1)

                    if arch in self.ignore_extra_archs \
                        and package in self.ignore_extra_archs[arch] \
                        and parch in self.ignore_extra_archs[arch][package]:
                        continue

                    binaries.setdefault(arch, {})
                    if name in binaries[arch]:
                        duplicates.setdefault(arch, {})
                        duplicates[arch].setdefault(name, set()).add(package)
                        duplicates[arch][name].add(binaries[arch][name])

                        continue

                    binaries[arch][name] = package

        if save:
            args = ['{}:Staging'.format(self.api.project), 'dashboard', 'duplicate_binaries']
            previous = self.api.load_file_content(*args)
            current = yaml.dump(duplicates, default_flow_style=False)
            if current != previous:
                args.append(current)
                self.api.save_file_content(*args)
        else:
            pprint(duplicates)

# vim: sw=4 et
