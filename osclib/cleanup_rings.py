from xml.etree import cElementTree as ET

from osc.core import makeurl
from osc.core import http_GET


class CleanupRings(object):
    def __init__(self, apiurl):
        self.bin2src = dict()
        self.pkgdeps = dict()
        self.sources = list()
        self.apiurl = apiurl
        self.links = dict()

    def perform(self):
        self.check_depinfo_ring('openSUSE:Factory:Rings:0-Bootstrap', 'openSUSE:Factory:Rings:1-MinimalX')
        self.check_depinfo_ring('openSUSE:Factory:Rings:1-MinimalX', 'openSUSE:Factory:MainDesktops')

    def find_inner_ring_links(self, prj):
        url = makeurl(self.apiurl, ['source', prj], { 'view': 'info', 'nofilename': '1' })
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for si in root.findall('sourceinfo'):
            linked = si.find('linked')
            if not linked is None and linked.get('project') != 'openSUSE:Factory':
                if not linked.get('project').startswith('openSUSE:Factory:Rings:'):
                    print(ET.tostring(si))
                self.links[linked.get('package')] = si.get('package')

    def fill_pkgdeps(self, prj, repo, arch):
        url = makeurl(self.apiurl, ['build', prj, repo, arch, '_builddepinfo'])
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for package in root.findall('package'):
            source = package.find('source').text
            if package.attrib['name'].startswith('preinstall'):
                continue
            self.sources.append(source)

            for subpkg in package.findall('subpkg'):
                subpkg = subpkg.text
                if self.bin2src.has_key(subpkg):
                    print('Binary {} is defined twice: {}/{}'.format(subpkg, prj, source))
                self.bin2src[subpkg] = source

        for package in root.findall('package'):
            source = package.find('source').text
            for pkg in package.findall('pkgdep'):
                if not self.bin2src.has_key(pkg.text):
                    if pkg.text.startswith('texlive-'):
                        for letter in range(ord('a'), ord('z') + 1):
                            self.pkgdeps['texlive-specs-' + chr(letter)] = 'texlive-specs-' + chr(letter)
                    else:
                        print('Package {} not found in place'.format(pkg.text))
                    continue
                b = self.bin2src[pkg.text]
                self.pkgdeps[b] = source

    def check_depinfo_ring(self, prj, nextprj):
        url = makeurl(self.apiurl, ['build', prj, '_result'] )
        root = ET.parse(http_GET(url)).getroot()
        for repo in root.findall('result'):
            repostate = repo.get('state', 'missing')
            if not repostate in ['unpublished', 'published']:
                print('Repo {}/{} is in state {}'.format(repo.get('project'), repo.get('repository'), repostate))
                return False
            for package in repo.findall('status'):
                code = package.get('code')
                if not code in ['succeeded', 'excluded']:
                    print('Package {}/{}/{} is {}'.format(repo.get('project'), repo.get('repository'), package.get('package'), code))
                    return False

        self.find_inner_ring_links(prj)
        self.fill_pkgdeps(prj, 'standard', 'x86_64')

        if prj == 'openSUSE:Factory:Rings:1-MinimalX':
            url = makeurl(self.apiurl, ['build', prj, 'images', 'x86_64', 'Test-DVD-x86_64', '_buildinfo'] )
            root = ET.parse(http_GET(url)).getroot()
            for bdep in root.findall('bdep'):
                if not bdep.attrib.has_key('name'):
                    continue
                b = bdep.attrib['name']
                if not self.bin2src.has_key(b):
                    continue
                b = self.bin2src[b]
                self.pkgdeps[b] = 'MYdvd'

        # if ($prj eq 'openSUSE:Factory:MainDesktops') {
        #   $dinfo->{MYcds} = {};
        #   $dinfo->{MYcds}->{pkgdep} = ();
        #   $dinfo->{MYcds}->{source} = 'MYcds';
        #   push(@{$dinfo->{MYcds}->{pkgdep}}, 'kiwi-image-livecd-gnome');
        #   push(@{$dinfo->{MYcds}->{pkgdep}}, 'kiwi-image-livecd-kde');

        if prj == 'openSUSE:Factory:Rings:0-Bootstrap':
            url = makeurl(self.apiurl, ['build', prj, 'standard', '_buildconfig'] )
            for line in http_GET(url).read().split('\n'):
                if line.startswith('Preinstall:') or line.startswith('Support:'):
                    for prein in line.split(':')[1].split():
                        if not self.bin2src.has_key(prein): continue
                        b = self.bin2src[prein]
                        self.pkgdeps[b] = 'MYinstall'

        for source in self.sources:
            if not self.pkgdeps.has_key(source) and not self.links.has_key(source):
                print('osc rdelete -m cleanup {} {}'.format(prj, source))
                if nextprj:
                    print('osc linkpac openSUSE:Factory {} {}').format(source, nextprj)
