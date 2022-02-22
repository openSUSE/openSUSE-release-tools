#!/usr/bin/python3

import argparse
import logging
import sys


import re
from lxml import etree as ET

import osc.conf
import osc.core
from osc.core import http_GET
from osc.core import makeurl
import osclib
from osclib.core import source_file_ensure
from osclib.conf import Config

SUPPORTED_ARCHS = ['x86_64', 'i586', 'aarch64', 'ppc64le', 's390x']
DEFAULT_REPOSITORY = 'standard'

META_PACKAGE = '000package-groups'


class SkippkgFinder(object):
    def __init__(self, opensuse_project, sle_project, alternative_project, print_only, verbose):
        self.upload_project = opensuse_project
        self.opensuse_project = opensuse_project
        if alternative_project:
            self.opensuse_project = alternative_project
        self.sle_project = sle_project
        self.print_only = print_only
        self.verbose = verbose
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']

        config = Config.get(self.apiurl, opensuse_project)
        # binary rpms of packages from `skippkg-finder-skiplist-ignores`
        # be found in the `package_binaries` thus format must to be like
        # SUSE:SLE-15:Update_libcdio.12032, PROJECT-NAME_PACKAGE-NAME
        self.skiplist_ignored = set(config.get('skippkg-finder-skiplist-ignores', '').split(' '))

        # supplement RPMs for skipping from the ftp-tree
        self.skiplist_supplement_regex = set(config.get('skippkg-finder-skiplist-supplement-regex', '').split(' '))
        # drops off RPM from a list of the supplement RPMs due to regex
        self.skiplist_supplement_ignores = set(config.get('skippkg-finder-skiplist-supplement-ignores', '').split(' '))

    def is_sle_specific(self, package):
        """
        Return True if package is provided for SLE only or a SLE forking.
        Add new condition here if you do not want package being added to
        selected_binarylist[].
        """
        pkg = package.lower()
        prefixes = (
            'desktop-data',
            'libyui-bindings',
            'libyui-doc',
            'libyui-ncurses',
            'libyui-qt',
            'libyui-rest',
            'lifecycle-data-sle',
            'kernel-livepatch',
            'kiwi-template',
            'mgr-',
            'migrate',
            'patterns',
            'release-notes',
            'sap',
            'sca-',
            'skelcd',
            'sle-',
            'sle_',
            'sle15',
            'sles15',
            'spacewalk',
            'supportutils-plugin',
            'suse-migration',
            'susemanager-',
            'yast2-hana',
        )
        suffixes = ('-caasp', '-sle', 'bootstrap')
        matches = (
            'gtk-vnc2',
            'ibus-googlepinyin',
            'infiniband-diags',
            'llvm',
            'lua51-luajit',
            'lvm2-clvm',
            'osad',
            'rhncfg',
            'python-ibus',
            'python-pymemcache',
            'suse-build-key',
            'suse-hpc',
            'txt2tags',
            'zypp-plugin-spacewalk',
            'zypper-search-packages-plugin',
        )
        if pkg.startswith(prefixes) or pkg.endswith(suffixes) or pkg in matches:
            return True
        if (
            'sles' in pkg
            or 'sled' in pkg
            or 'sap-' in pkg
            or '-sap' in pkg
            or 'eula' in pkg
            or 'branding' in pkg
        ):
            return True
        return False

    def get_packagelist(self, project, by_project=True):
        """
        Return the list of package's info of a project.
        If the latest package is from an incident then returns incident
        package.
        """

        pkglist = {}
        packageinfo = {}
        query = {'expand': 1}
        root = ET.parse(http_GET(makeurl(self.apiurl, ['source', project],
                                 query=query))).getroot()
        for i in root.findall('entry'):
            pkgname = i.get('name')
            orig_project = i.get('originproject')
            is_incidentpkg = False
            # Metapackage should not be selected
            if pkgname.startswith('000') or\
                    pkgname.startswith('_') or\
                    pkgname.startswith('patchinfo.') or\
                    pkgname.startswith('skelcd-') or\
                    pkgname.startswith('installation-images') or\
                    pkgname.startswith('Leap-release') or\
                    pkgname.endswith('-mini') or\
                    '-mini.' in pkgname:
                continue
            # Ugly hack for package has dot in source package name
            # eg. go1.x incidents as the name would be go1.x.xxx
            if '.' in pkgname and re.match(r'[0-9]+$', pkgname.split('.')[-1]) and \
                    orig_project.startswith('SUSE:') and orig_project.endswith(':Update'):
                is_incidentpkg = True
                if pkgname.startswith('go1') or\
                        pkgname.startswith('bazel0') or\
                        pkgname.startswith('dotnet') or\
                        pkgname.startswith('rust1') or\
                        pkgname.startswith('ruby2'):
                    if not (pkgname.count('.') > 1):
                        is_incidentpkg = False

            # If an incident found then update the package origin info
            if is_incidentpkg:
                orig_name = re.sub(r'\.[0-9]+$', '', pkgname)
                incident_number = int(pkgname.split('.')[-1])
                if orig_name in pkglist and pkglist[orig_name]['Project'] == orig_project:
                    if re.match(r'[0-9]+$', pkglist[orig_name]['Package'].split('.')[-1]):
                        old_incident_number = int(pkglist[orig_name]['Package'].split('.')[-1])
                        if incident_number > old_incident_number:
                            pkglist[orig_name]['Package'] = pkgname
                    else:
                        pkglist[orig_name]['Package'] = pkgname
            else:
                pkglist[pkgname] = {'Project': orig_project, 'Package': pkgname}

        if by_project:
            for pkg in pkglist.keys():
                if pkglist[pkg]['Project'].startswith('SUSE:') and self.is_sle_specific(pkg):
                    continue
                if pkglist[pkg]['Project'] not in packageinfo:
                    packageinfo[pkglist[pkg]['Project']] = []
                if pkglist[pkg]['Package'] not in packageinfo[pkglist[pkg]['Project']]:
                    packageinfo[pkglist[pkg]['Project']].append(pkglist[pkg]['Package'])
            return packageinfo

        return pkglist

    def get_project_binary_list(self, project, repository, arch, package_binaries={}):
        """
        Returns binarylist of a project
        """

        # Use pool repository for SUSE namespace project.
        # Because RPMs were injected to pool repository on OBS rather than
        # standard repository.
        if project.startswith('SUSE:'):
            repository = 'pool'

        path = ['build', project, repository, arch]
        url = makeurl(self.apiurl, path, {'view': 'binaryversions'})
        root = ET.parse(http_GET(url)).getroot()

        for binary_list in root:
            package = binary_list.get('package')
            package = package.split(':', 1)[0]
            index = project + "_" + package

            if index not in package_binaries:
                package_binaries[index] = []
            for binary in binary_list:
                filename = binary.get('name')
                result = re.match(osclib.core.RPM_REGEX, filename)
                if not result:
                    continue

                if result.group('arch') == 'src' or result.group('arch') == 'nosrc':
                    continue
                if result.group('name').endswith('-debuginfo') or result.group('name').endswith('-debuginfo-32bit'):
                    continue
                if result.group('name').endswith('-debugsource'):
                    continue

                if result.group('name') not in package_binaries[index]:
                    package_binaries[index].append(result.group('name'))

        return package_binaries

    def exception_package(self, package):
        """
        Do not skip the package if matches the condition.
        package parameter is source package name.
        """

        if '-bootstrap' in package or\
                'Tumbleweed' in package or\
                'metis' in package:
            return True
        # These packages must have a good reason not to be single-speced
        # from one source.
        if package.startswith('python2-') or\
                package.startswith('python3'):
            return True
        return False

    def exception_binary(self, package):
        """
        Do not skip the binary if matches the condition
        package parameter is RPM filename.
        """

        if package == 'openSUSE-release' or\
                package == 'openSUSE-release-ftp' or\
                package == 'openSUSE-Addon-NonOss-release':
            return True
        return False

    def crawl(self):
        """Main method"""

        leap_pkglist = self.get_packagelist(self.opensuse_project)
        sle_pkglist = self.get_packagelist(self.sle_project, by_project=False)
        # The selected_binarylist[] includes the latest sourcepackage list
        # binary RPMs from the latest sources need to be presented in ftp eventually
        selected_binarylist = []
        # Any existed binary RPMs from any SPx/Leap/Backports
        fullbinarylist = []
        # package_binaries[] is a pre-formated binarylist per each package
        # access to the conotent uses package_binaries['SUSE:SLE-15:Update_libcdio.12032']
        package_binaries = {}

        # Inject binarylist to a list per package name no matter what archtectures was
        for arch in SUPPORTED_ARCHS:
            for prj in leap_pkglist.keys():
                package_binaries = self.get_project_binary_list(prj, DEFAULT_REPOSITORY, arch, package_binaries)

        for pkg in package_binaries.keys():
            if not self.exception_package(pkg):
                fullbinarylist += package_binaries[pkg]

        for prj in leap_pkglist.keys():
            for pkg in leap_pkglist[prj]:
                cands = [prj + "_" + pkg]
                # Handling for SLE forks, or package has different multibuild bits
                # enablility between SLE and openSUSE
                if prj.startswith('openSUSE:') and pkg in sle_pkglist and\
                        not self.is_sle_specific(pkg):
                    cands.append(sle_pkglist[pkg]['Project'] + "_" + sle_pkglist[pkg]['Package'])
                logging.debug(cands)
                for index in cands:
                    if index in package_binaries:
                        selected_binarylist += package_binaries[index]
                    else:
                        logging.info("Can not find binary of %s" % index)

        # Some packages has been obsoleted by new updated package, however
        # there are application still depend on old library when it builds
        # eg. SUSE:SLE-15-SP3:GA has qpdf/libqpdf28 but cups-filter was build
        # in/when SLE15 SP2 which requiring qpdf/libqpdf6, therefore old
        # qpdf/libqpdf6 from SLE15 SP2 should not to be missed.
        for pkg in self.skiplist_ignored:
            selected_binarylist += package_binaries[pkg]

        # Preparing a packagelist for the skipping candidate
        obsoleted = []
        for pkg in fullbinarylist:
            if pkg not in selected_binarylist and pkg not in obsoleted:
                if not self.exception_binary(pkg):
                    obsoleted.append(pkg)

        # Post processing of obsoleted packagelist
        tmp_obsoleted = obsoleted.copy()
        for pkg in tmp_obsoleted:
            # Respect to single-speced python package, when a python2 RPM is
            # considered then a python3 flavor should also be selected to be
            # skipped, if not, don't add it.
            if pkg.startswith('python2-') and re.sub(r'^python2', 'python3', pkg) not in obsoleted:
                obsoleted.remove(pkg)
            # Main RPM must to be skipped if -32 bit RPM or -64bit RPM is
            # considered.
            if pkg.endswith('-32bit') or pkg.endswith('-64bit'):
                main_filename = re.sub('-[36][24]bit', '', pkg)
                if main_filename not in obsoleted:
                    obsoleted.remove(pkg)

        for regex in self.skiplist_supplement_regex:
            # exit if it has no regex defined
            if not regex:
                break
            for binary in fullbinarylist:
                result = re.match(regex, binary)
                if result and binary not in obsoleted and\
                        binary not in self.skiplist_supplement_ignores:
                    obsoleted.append(binary)

        skip_list = ET.Element('group', {'name': 'NON_FTP_PACKAGES'})
        ET.SubElement(skip_list, 'conditional', {'name': 'drop_from_ftp'})
        packagelist = ET.SubElement(skip_list, 'packagelist', {'relationship': 'requires'})
        for pkg in sorted(obsoleted):
            if not self.print_only and self.verbose:
                print(pkg)
            attr = {'name': pkg}
            ET.SubElement(packagelist, 'package', attr)
        if not self.print_only:
            source_file_ensure(self.apiurl, self.upload_project, META_PACKAGE, 'NON_FTP_PACKAGES.group',
                               ET.tostring(skip_list, pretty_print=True, encoding='unicode'),
                               'Update the skip list')
        else:
            print(ET.tostring(skip_list, pretty_print=True,
                  encoding='unicode'))


def main(args):
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    if args.opensuse_project is None or args.sle_project is None:
        print("Please pass --opensuse-project and --sle-project argument. See usage with --help.")
        quit()

    uc = SkippkgFinder(args.opensuse_project, args.sle_project, args.alternative_project, args.print_only, args.verbose)
    uc.crawl()


if __name__ == '__main__':
    description = 'Overwrites NON_FTP_PACKAGES.group according to the latest sources. '\
                  'This tool only works for Leap after CtLG implemented.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-o', '--opensuse-project', dest='opensuse_project', metavar='OPENSUSE_PROJECT',
                        help='openSUSE project on buildservice')
    parser.add_argument('-s', '--sle-project', dest='sle_project', metavar='SLE_PROJECT',
                        help='SLE project on buildservice')
    parser.add_argument('-t', '--alternative-project', dest='alternative_project', metavar='ALTERNATIVE_PROJECT',
                        help='check the given project instead of OPENSUSE_PROJECT')
    parser.add_argument('-p', '--print-only', action='store_true',
                        help='show the result instead of the uploading')
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='show the diff')

    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
