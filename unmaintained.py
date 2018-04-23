#!/usr/bin/python

import argparse
from lxml import etree as ET
from osc import conf
from osc.core import meta_get_filelist
from osclib.core import package_binary_list
from osclib.core import source_file_load
import sys
import yaml

def kiwi_binaries(apiurl, project):
    binaries = set()
    for filename in meta_get_filelist(apiurl, project, '000product'):
        if not filename.endswith('.kiwi'):
            continue

        kiwi = ET.fromstring(source_file_load(
            apiurl, project, '000product', filename))

        binaries.update(kiwi.xpath('//instsource/repopackages/repopackage/@name'))

    return binaries

def unmaintained(apiurl, project_target):
    lookup = yaml.safe_load(source_file_load(
        apiurl, project_target, '00Meta', 'lookup.yml'))
    lookup_total = len(lookup)
    lookup = {k: v for k, v in lookup.iteritems() if v.startswith('SUSE:SLE')}

    package_binaries, _ = package_binary_list(
        apiurl, project_target, 'standard', 'x86_64', exclude_src_debug=True)
    package_binaries_total = len(package_binaries)
    package_binaries = [pb for pb in package_binaries if pb.package in lookup]

    # Determine max length possible for each column.
    maxes = [
        len(max([b.name for b in package_binaries], key=len)),
        len(max(lookup.keys(), key=len)),
        len(max(lookup.values(), key=len)),
    ]
    line_format = ' '.join(['{:<' + str(m) + '}' for m in maxes])

    print(line_format.format('binary', 'package', 'source project'))

    project_sources = {}
    binaries_unmaintained = 0
    packages_unmaintained = set()
    for package_binary in sorted(package_binaries, key=lambda pb: pb.name):
        project_source = lookup[package_binary.package]
        if project_source not in project_sources:
            # Load binaries referenced in kiwi the first time source encountered.
            project_sources[project_source] = kiwi_binaries(apiurl, project_source)

        if package_binary.name not in project_sources[project_source]:
            print(line_format.format(
                package_binary.name, package_binary.package, project_source))

            binaries_unmaintained += 1
            packages_unmaintained.add(package_binary.package)

    print('{:,} of {:,} binaries ({:,} packages) unmaintained from SLE of {:,} total binaries ({:,} packages) in project'.format(
        binaries_unmaintained, len(package_binaries), len(packages_unmaintained), package_binaries_total, lookup_total))

def main(args):
    conf.get_config(override_apiurl=args.apiurl)
    conf.config['debug'] = args.debug
    apiurl = conf.config['apiurl']

    return not unmaintained(apiurl, args.project_target)


if __name__ == '__main__':
    description = 'Review each binary in target project sourced from SLE to see if utilized in kiwi files.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', help='OBS instance API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print useful debugging info')
    parser.add_argument('project_target', help='target project to search')
    args = parser.parse_args()

    sys.exit(main(args))
