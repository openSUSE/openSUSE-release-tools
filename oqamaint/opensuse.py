# -*- coding: utf-8 -*-

from update import Update
import re
from collections import namedtuple
import osc.core

try:
    from xml.etree import cElementTree as ET
except ImportError:
    from xml.etree import ElementTree as ET

Package = namedtuple('Package', ('name', 'version', 'release'))

class openSUSEUpdate(Update):

    repo_prefix = 'http://download.opensuse.org/repositories'
    maintenance_project = 'openSUSE:Maintenance'

    def package_details(self, prj, repo, arch, binary):
        url = osc.core.makeurl(
            self.apiurl,
            ('build', prj, repo, arch, '_repository', binary),
            query={'view': 'fileinfo'})

        root = ET.parse(osc.core.http_GET(url)).getroot()
        return Package(root.find('.//name').text,
                       root.find('.//version').text,
                       root.find('.//release').text)

    # list all packages released for an incident
    def packages(self, src_prj, dst_prj):
        packages = dict()
        repo = dst_prj.replace(':', '_')
        # patchinfo collects the binaries and is build for an
        # unpredictable architecture so we need iterate over all
        url = osc.core.makeurl(self.apiurl, ('build', src_prj, repo))
        root = ET.parse(osc.core.http_GET(url)).getroot()
        for arch in [n.attrib['name'] for n in root.findall('entry')]:
            query = {'nosource': 1}
            url = osc.core.makeurl(
                self.apiurl,
                ('build', src_prj, repo, arch, '_repository'),
                query=query)

            root = ET.parse(osc.core.http_GET(url)).getroot()

            for binary in root.findall('binary'):
                b = binary.attrib['filename']
                if b.endswith('.rpm'):
                    p = self.package_details(src_prj, repo, arch, b)
                    packages[p.name] = p

        return packages

    def settings(self, src_prj, dst_prj):
        # strip the architecture for openSUSE - we do them all in one
        dst_prj = re.sub(r':x86_64$', '', dst_prj)
        settings = super(openSUSEUpdate, self).settings(src_prj, dst_prj)
        settings = settings[0]

        # openSUSE:Maintenance key
        settings['IMPORT_GPG_KEYS'] = 'gpg-pubkey-b3fd7e48-5549fd0f'
        settings['ZYPPER_ADD_REPO_PREFIX'] = 'incident'

        packages = self.packages(src_prj, dst_prj)
        settings['INSTALL_PACKAGES'] = ' '.join(packages.keys())
        settings['VERIFY_PACKAGE_VERSIONS'] = ' '.join(
                ['{} {}-{}'.format(p.name, p.version, p.release) for p in packages.values()])

        return [settings]
