# Copyright (C) 2015 SUSE Linux GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import time
import re
from xml.etree import cElementTree as ET


class FreezeCommand(object):

    def __init__(self, api):
        self.api = api
        self.projectlinks = []

    def set_links(self):
        url = self.api.makeurl(['source', self.prj, '_meta'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        links = root.findall('link')
        links.reverse()
        self.projectlinks = [link.get('project') for link in links]

    def set_bootstrap_copy(self):
        url = self.api.makeurl(['source', self.prj, '_meta'])

        f = self.api.retried_GET(url)
        oldmeta = ET.parse(f).getroot()

        meta = ET.fromstring(self.prj_meta_for_bootstrap_copy(self.prj))
        meta.find('title').text = oldmeta.find('title').text
        meta.find('description').text = oldmeta.find('description').text

        self.api.retried_PUT(url, ET.tostring(meta))

    def create_bootstrap_aggregate(self):
        self.create_bootstrap_aggregate_meta()
        self.create_bootstrap_aggregate_file()

    def bootstrap_packages(self):
        url = self.api.makeurl(['source', '{}:0-Bootstrap'.format(self.api.crings)])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        l = list()
        for e in root.findall('entry'):
            name = e.get('name')
            if name in ['rpmlint-mini-AGGR']:
                continue
            l.append(name)
        l.sort()
        return l

    def create_bootstrap_aggregate_file(self):
        url = self.api.makeurl(['source', self.prj, 'bootstrap-copy', '_aggregate'])

        root = ET.Element('aggregatelist')
        a = ET.SubElement(root, 'aggregate',
                          {'project': '{}:0-Bootstrap'.format(self.api.crings)})

        for package in self.bootstrap_packages():
            p = ET.SubElement(a, 'package')
            p.text = package

        ET.SubElement(a, 'repository', {'target': 'bootstrap_copy', 'source': 'standard'})
        ET.SubElement(a, 'repository', {'target': 'standard', 'source': 'nothing'})
        ET.SubElement(a, 'repository', {'target': 'images', 'source': 'nothing'})

        self.api.retried_PUT(url, ET.tostring(root))

    def create_bootstrap_aggregate_meta(self):
        url = self.api.makeurl(['source', self.prj, 'bootstrap-copy', '_meta'])

        root = ET.Element('package', {'project': self.prj, 'name': 'bootstrap-copy'})
        ET.SubElement(root, 'title')
        ET.SubElement(root, 'description')
        f = ET.SubElement(root, 'build')
        # this one is to toggle
        ET.SubElement(f, 'disable', {'repository': 'bootstrap_copy'})
        # this one is the global toggle
        ET.SubElement(f, 'disable')

        self.api.retried_PUT(url, ET.tostring(root))

    def build_switch_bootstrap_copy(self, state):
        url = self.api.makeurl(['source', self.prj, 'bootstrap-copy', '_meta'])
        pkgmeta = ET.parse(self.api.retried_GET(url)).getroot()

        for f in pkgmeta.find('build'):
            if f.get('repository', None) == 'bootstrap_copy':
                f.tag = state
                pass
        self.api.retried_PUT(url, ET.tostring(pkgmeta))

    def verify_bootstrap_copy_codes(self, codes):
        url = self.api.makeurl(['build', self.prj, '_result'], {'package': 'bootstrap-copy'})

        root = ET.parse(self.api.retried_GET(url)).getroot()
        for result in root.findall('result'):
            if result.get('repository') == 'bootstrap_copy':
                status = result.find('status')
                if status is None:
                    return False
                if not status.get('code') in codes:
                    return False
        return True

    def perform(self, prj):
        self.prj = prj
        self.set_links()

        self.freeze_prjlinks()

        build_status = self.api.get_flag_in_prj(prj, flag='build')

        # If there is not a bootstrap repository, there is not
        # anything more to do.
        if not self.is_bootstrap():
            return

        self.set_bootstrap_copy()
        self.create_bootstrap_aggregate()
        print("waiting for scheduler to disable...")
        while not self.verify_bootstrap_copy_codes(['disabled']):
            time.sleep(1)
        self.build_switch_bootstrap_copy('enable')
        print("waiting for scheduler to copy...")
        while not self.verify_bootstrap_copy_codes(['finished', 'succeeded']):
            time.sleep(1)
        self.build_switch_bootstrap_copy('disable')

        # Update the version information found in the Test-DVD package, to match openSUSE-release
        if self.api.item_exists(prj, "openSUSE-release"):
            version = self.api.package_version(prj, 'openSUSE-release')
            for arch in ['x86_64', 'ppc64le']:
                self.update_product_version(prj, 'Test-DVD-' + arch, arch, version)

        # now try to freeze sub project - much easier
        if self.api.item_exists(prj + ':DVD') and self.api.item_exists(prj, "openSUSE-release"):
            self.prj = prj + ':DVD'
            self.set_links()
            self.freeze_prjlinks()

            # Update the version information found in the Test-DVD package, to match openSUSE-release
            version = self.api.package_version(prj, 'openSUSE-release')
            for arch in ['x86_64', 'ppc64le']:
                self.update_product_version(prj + ':DVD', 'Test-DVD-' + arch, arch, version)

        # Set the original build status for the project
        self.api.build_switch_prj(prj, build_status)

    def update_product_version(self, project, product, arch, version):
        if not self.api.item_exists(project, product):
            return None

        kiwifile = self.api.load_file_content(project, product, 'PRODUCT-'+arch+'.kiwi')

        tmpkiwifile = re.sub(r'<productinfo name="VERSION">.*</productinfo>', '<productinfo name="VERSION">%s</productinfo>' % version, kiwifile)
        newkiwifile = re.sub(r'<productvar name="VERSION">.*</productvar>', '<productvar name="VERSION">%s</productvar>' % version, tmpkiwifile)

        self.api.save_file_content(project, product, 'PRODUCT-' + arch + '.kiwi', newkiwifile)

    def prj_meta_for_bootstrap_copy(self, prj):
        root = ET.Element('project', {'name': prj})
        ET.SubElement(root, 'title')
        ET.SubElement(root, 'description')
        links = self.projectlinks or ['{}:1-MinimalX'.format(self.api.crings)]
        for lprj in links:
            ET.SubElement(root, 'link', {'project': lprj})
        f = ET.SubElement(root, 'build')
        # this one stays
        ET.SubElement(f, 'disable', {'repository': 'bootstrap_copy'})
        # this one is the global toggle
        ET.SubElement(f, 'disable')
        f = ET.SubElement(root, 'publish')
        ET.SubElement(f, 'disable')
        f = ET.SubElement(root, 'debuginfo')
        ET.SubElement(f, 'enable')

        r = ET.SubElement(root, 'repository', {'name': 'bootstrap_copy'})
        ET.SubElement(r, 'path', {'project': self.api.cstaging, 'repository': 'standard'})
        for arch in self.api.cstaging_archs:
            a = ET.SubElement(r, 'arch')
            a.text = arch

        r = ET.SubElement(root, 'repository', {'name': 'standard', 'linkedbuild': 'all', 'rebuild': 'direct'})
        ET.SubElement(r, 'path', {'project': prj, 'repository': 'bootstrap_copy'})
        for arch in self.api.cstaging_archs:
            a = ET.SubElement(r, 'arch')
            a.text = arch

        r = ET.SubElement(root, 'repository', {'name': 'images', 'linkedbuild': 'all', 'rebuild': 'direct'})
        ET.SubElement(r, 'path', {'project': prj, 'repository': 'standard'})
        a = ET.SubElement(r, 'arch')
        a.text = 'x86_64'

        if 'ppc64le' in self.api.cstaging_archs:
            a = ET.SubElement(r, 'arch')
            a.text = 'ppc64le'

        return ET.tostring(root)

    def freeze_prjlinks(self):
        sources = {}
        flink = ET.Element('frozenlinks')

        for lprj in self.projectlinks:
            fl = ET.SubElement(flink, 'frozenlink', {'project': lprj})
            sources = self.receive_sources(lprj, sources, fl)

        url = self.api.makeurl(['source', self.prj, '_project', '_frozenlinks'], {'meta': '1'})
        self.api.retried_PUT(url, ET.tostring(flink))

    def receive_sources(self, prj, sources, flink):
        url = self.api.makeurl(['source', prj], {'view': 'info', 'nofilename': '1'})
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()

        for si in root.findall('sourceinfo'):
            package = self.check_one_source(flink, si)
            sources[package] = 1
        return sources

    def check_one_source(self, flink, si):
        package = si.get('package')

        # If the package is an internal one (e.g _product)
        if package.startswith('_'):
            return None

        # Ignore packages with an origing (i.e. with an origin
        # different from the current project)
        if si.find('originproject') != None:
            return None

        # we have to check if its a link within the staging project
        # in this case we need to keep the link as is, and not freezing
        # the target. Otherwise putting kernel-source into staging prj
        # won't get updated kernel-default (and many other cases)
        for linked in si.findall('linked'):
            if linked.get('project') in self.projectlinks:
                # take the unexpanded md5 from Factory / 13.2 link
                url = self.api.makeurl(['source', self.api.project, package],
                                       {'view': 'info', 'nofilename': '1'})
                # print(package, linked.get('package'), linked.get('project'))
                f = self.api.retried_GET(url)
                proot = ET.parse(f).getroot()
                lsrcmd5 = proot.get('lsrcmd5')
                if lsrcmd5 is None:
                    raise Exception("{}/{} is not a link but we expected one".format(self.api.project, package))
                ET.SubElement(flink, 'package', {'name': package, 'srcmd5': lsrcmd5, 'vrev': si.get('vrev')})
                return package
        if package in ['rpmlint-mini-AGGR']:
            return package  # we should not freeze aggregates
        ET.SubElement(flink, 'package', {'name': package, 'srcmd5': si.get('srcmd5'), 'vrev': si.get('vrev')})
        return package

    def is_bootstrap(self):
        """Check if there is a bootstrap copy repository."""
        url = self.api.makeurl(['source', self.prj, '_meta'])
        root = ET.parse(self.api.retried_GET(url)).getroot()

        for repo in root.findall('.//repository'):
            if 'bootstrap_copy' == repo.get('name'):
                return True
        return False
