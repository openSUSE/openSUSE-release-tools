from xml.etree import cElementTree as ET

from osc.core import makeurl
from osc.core import http_GET


class CleanupRings(object):
    def __init__(self, api):
        self.bin2src = {}
        self.pkgdeps = {}
        self.sources = set()
        self.api = api
        self.links = {}
        self.commands = []
        self.whitelist = [
            # Must remain in ring-1 with other kernel packages to keep matching
            # build number, but is required by virtualbox in ring-2.
            'kernel-syms',
        ]

    def perform(self):
        for index, ring in enumerate(self.api.rings):
            print('# {}'.format(ring))
            ring_next = self.api.rings[index + 1] if index + 1 < len(self.api.rings) else None
            self.check_depinfo_ring(ring, ring_next)

        print('\n'.join(self.commands))

    def find_inner_ring_links(self, prj):
        query = {
            'view': 'info',
            'nofilename': '1'
        }
        url = makeurl(self.api.apiurl, ['source', prj], query=query)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for si in root.findall('sourceinfo'):
            links = si.findall('linked')
            pkg = si.get('package')
            if links is None or len(links) == 0:
                print '# {} not a link'.format(pkg)
            else:
                linked = links[0]
                dprj = linked.get('project')
                dpkg = linked.get('package')
                if dprj != self.api.project:
                    if not dprj.startswith(self.api.crings):
                        print "#{} not linking to base {} but {}".format(pkg, self.api.project, dprj)
                    self.links[dpkg] = pkg
                # multi spec package must link to ring
                elif len(links) > 1:
                    mainpkg = links[1].get('package')
                    mainprj = links[1].get('project')
                    if mainprj != self.api.project:
                        print '# FIXME: {} links to {}'.format(pkg, mainprj)
                    else:
                        destring = None
                        if mainpkg in self.api.ring_packages:
                            destring = self.api.ring_packages[mainpkg]
                        if not destring:
                            print '# {} links to {} but is not in a ring'.format(pkg, mainpkg)
                            print "osc linkpac {}/{} {}/{}".format(mainprj, mainpkg, prj, mainpkg)
                        else:
                            if pkg != 'glibc.i686': # FIXME: ugly exception
                                print "osc linkpac -f {}/{} {}/{}".format(destring, mainpkg, prj, pkg)
                                self.links[mainpkg] = pkg


    def fill_pkgdeps(self, prj, repo, arch):
        url = makeurl(self.api.apiurl, ['build', prj, repo, arch, '_builddepinfo'])
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for package in root.findall('package'):
            # use main package name for multibuild. We can't just ignore
            # multibuild as eg installation-images has no results for the main
            # package itself
            # https://github.com/openSUSE/open-build-service/issues/4198
            name = package.attrib['name'].split(':')[0]
            if name.startswith('preinstall'):
                continue

            source = package.find('source').text
            if source != name:
                print ("WARN {} != {}".format(source, name))
            self.sources.add(name)

            for subpkg in package.findall('subpkg'):
                subpkg = subpkg.text
                if subpkg in self.bin2src:
                    if self.bin2src[subpkg] == name:
                        # different archs
                        continue
                    print('Binary {} is defined twice: {}/{}'.format(subpkg, prj, name))
                self.bin2src[subpkg] = name

        for package in root.findall('package'):
            name = package.attrib['name'].split(':')[0]
            for pkg in package.findall('pkgdep'):
                if pkg.text not in self.bin2src:
                    if not pkg.text.startswith('texlive-'): # XXX: texlive bullshit packaging
                        print('Package {} not found in place'.format(pkg.text))
                    continue
                b = self.bin2src[pkg.text]
                self.pkgdeps[b] = name

    def repo_state_acceptable(self, project):
        url = makeurl(self.api.apiurl, ['build', project, '_result'])
        root = ET.parse(http_GET(url)).getroot()
        for repo in root.findall('result'):
            repostate = repo.get('state', 'missing')
            if repostate not in ['unpublished', 'published'] or repo.get('dirty', 'false') == 'true':
                print('Repo {}/{} is in state {}'.format(repo.get('project'), repo.get('repository'), repostate))
                return False
            for package in repo.findall('status'):
                code = package.get('code')
                if code not in ['succeeded', 'excluded', 'disabled']:
                    print('Package {}/{}/{} is {}'.format(repo.get('project'), repo.get('repository'), package.get('package'), code))
                    return False
        return True

    def check_image_bdeps(self, project, arch):
        url = makeurl(self.api.apiurl, ['build', project, 'images', arch, 'Test-DVD-' + arch, '_buildinfo'])
        root = ET.parse(http_GET(url)).getroot()
        for bdep in root.findall('bdep'):
            if 'name' not in bdep.attrib:
                continue
            b = bdep.attrib['name']
            if b not in self.bin2src:
                continue
            b = self.bin2src[b]
            self.pkgdeps[b] = 'MYdvd{}'.format(self.api.rings.index(project))

    def check_buildconfig(self, project):
        url = makeurl(self.api.apiurl, ['build', project, 'standard', '_buildconfig'])
        for line in http_GET(url).read().splitlines():
            if line.startswith('Preinstall:') or line.startswith('Support:'):
                for prein in line.split(':')[1].split():
                    if prein not in self.bin2src:
                        continue
                    b = self.bin2src[prein]
                    self.pkgdeps[b] = 'MYinstall'

    def check_requiredby(self, project, package):
        # Prioritize x86_64 bit.
        for arch in reversed(self.api.ring_archs(project)):
            for fileinfo in self.api.fileinfo_ext_all(project, 'standard', arch, package):
                for requiredby in fileinfo.findall('provides_ext/requiredby[@name]'):
                    b = self.bin2src[requiredby.get('name')]
                    if b == package:
                        # A subpackage depending on self.
                        continue
                    self.pkgdeps[package] = b
                    return True
        return False

    def check_depinfo_ring(self, prj, nextprj):
        if not self.repo_state_acceptable(prj):
            return False

        self.find_inner_ring_links(prj)
        for arch in self.api.ring_archs(prj):
            self.fill_pkgdeps(prj, 'standard', arch)

        if self.api.rings.index(prj) == 0:
            self.check_buildconfig(prj)
        else: # Ring 1 or 2.
            # Always look at DVD archs for image, even in ring 1.
            for arch in self.api.cstaging_dvd_archs:
                self.check_image_bdeps(prj, arch)

        for source in self.sources:
            if (source not in self.pkgdeps and
                source not in self.links and
                source not in self.whitelist):
                if source.startswith('texlive-specs-'): # XXX: texlive bullshit packaging
                    continue
                # Expensive check so left until last.
                if self.check_requiredby(prj, source):
                    continue

                print('# - {}'.format(source))
                self.commands.append('osc rdelete -m cleanup {} {}'.format(prj, source))
                if nextprj:
                    self.commands.append('osc linkpac {} {} {}'.format(self.api.project, source, nextprj))

        # Only loop through sources once from their origin ring to ensure single
        # step moving to allow check_requiredby() to see result in each ring.
        self.sources = set()
