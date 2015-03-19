from xml.etree import cElementTree as ET

from osc.core import makeurl
from osc.core import http_GET


class CleanupRings(object):
    def __init__(self, api):
        self.bin2src = {}
        self.pkgdeps = {}
        self.sources = []
        self.api = api
        self.links = {}

    def perform(self):
        self.check_depinfo_ring('{}:0-Bootstrap'.format(self.api.crings),
                                '{}:1-MinimalX'.format(self.api.crings))
        self.check_depinfo_ring('{}:1-MinimalX'.format(self.api.crings),
                                '{}:2-TestDVD'.format(self.api.crings))
        self.check_depinfo_ring('{}:2-TestDVD'.format(self.api.crings), None)

    def find_inner_ring_links(self, prj):
        query = {
            'view': 'info',
            'nofilename': '1'
        }
        url = makeurl(self.api.apiurl, ['source', prj], query=query)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for si in root.findall('sourceinfo'):
            linked = si.find('linked')
            if linked is not None and linked.get('project') != self.api.project:
                if not linked.get('project').startswith(self.api.crings):
                    print(ET.tostring(si))
                self.links[linked.get('package')] = si.get('package')

    def fill_pkgdeps(self, prj, repo, arch):
        url = makeurl(self.api.apiurl, ['build', prj, repo, arch, '_builddepinfo'])
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for package in root.findall('package'):
            source = package.find('source').text
            if package.attrib['name'].startswith('preinstall'):
                continue
            self.sources.append(source)

            for subpkg in package.findall('subpkg'):
                subpkg = subpkg.text
                if subpkg in self.bin2src:
                    print('Binary {} is defined twice: {}/{}'.format(subpkg, prj, source))
                self.bin2src[subpkg] = source

        for package in root.findall('package'):
            source = package.find('source').text
            for pkg in package.findall('pkgdep'):
                if pkg.text not in self.bin2src:
                    if pkg.text.startswith('texlive-'):
                        for letter in range(ord('a'), ord('z') + 1):
                            self.pkgdeps['texlive-specs-' + chr(letter)] = 'texlive-specs-' + chr(letter)
                    else:
                        print('Package {} not found in place'.format(pkg.text))
                    continue
                b = self.bin2src[pkg.text]
                self.pkgdeps[b] = source

    def check_depinfo_ring(self, prj, nextprj):
        url = makeurl(self.api.apiurl, ['build', prj, '_result'])
        root = ET.parse(http_GET(url)).getroot()
        for repo in root.findall('result'):
            repostate = repo.get('state', 'missing')
            if repostate not in ['unpublished', 'published']:
                print('Repo {}/{} is in state {}'.format(repo.get('project'), repo.get('repository'), repostate))
                return False
            for package in repo.findall('status'):
                code = package.get('code')
                if code not in ['succeeded', 'excluded', 'disabled']:
                    print('Package {}/{}/{} is {}'.format(repo.get('project'), repo.get('repository'), package.get('package'), code))
                    return False

        self.find_inner_ring_links(prj)
        for arch in [ 'x86_64', 'ppc64le' ]:
            self.fill_pkgdeps(prj, 'standard', arch)

            if prj == '{}:1-MinimalX'.format(self.api.crings):
                url = makeurl(self.api.apiurl, ['build', prj, 'images', 'x86_64', 'Test-DVD-' + arch, '_buildinfo'])
                root = ET.parse(http_GET(url)).getroot()
                for bdep in root.findall('bdep'):
                    if 'name' not in bdep.attrib:
                        continue
                    b = bdep.attrib['name']
                    if b not in self.bin2src:
                        continue
                    b = self.bin2src[b]
                    self.pkgdeps[b] = 'MYdvd'

            if prj == '{}:2-TestDVD'.format(self.api.crings):
                url = makeurl(self.api.apiurl, ['build', prj, 'images', 'x86_64', 'Test-DVD-' + arch, '_buildinfo'])
                root = ET.parse(http_GET(url)).getroot()
                for bdep in root.findall('bdep'):
                    if 'name' not in bdep.attrib:
                        continue
                    b = bdep.attrib['name']
                    if b not in self.bin2src:
                        continue
                    b = self.bin2src[b]
                    self.pkgdeps[b] = 'MYdvd2'

        if prj == '{}:0-Bootstrap'.format(self.api.crings):
            url = makeurl(self.api.apiurl, ['build', prj, 'standard', '_buildconfig'])
            for line in http_GET(url).read().split('\n'):
                if line.startswith('Preinstall:') or line.startswith('Support:'):
                    for prein in line.split(':')[1].split():
                        if prein not in self.bin2src:
                            continue
                        b = self.bin2src[prein]
                        self.pkgdeps[b] = 'MYinstall'

        for source in self.sources:
            if source not in self.pkgdeps and source not in self.links:
                print('osc rdelete -m cleanup {} {}'.format(prj, source))
                if nextprj:
                    print('osc linkpac {} {} {}').format(self.api.project, source, nextprj)
