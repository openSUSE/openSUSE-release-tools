from lxml import etree as ET
from osc.core import makeurl
from osc.core import http_GET
from osclib.core import fileinfo_ext_all
from osclib.core import builddepinfo
from osclib.memoize import memoize


class CleanupRings(object):
    def __init__(self, api):
        self.bin2src = {}
        self.pkgdeps = {}
        self.sources = set()
        self.api = api
        self.links = {}
        self.commands = []
        self.whitelist = [
            # Keep this in ring 1, even though ring 0 builds the main flavor
            # and ring 1 has that disabled.
            'automake:testsuite',
            'meson:test',
            # buildtime services aren't visible in _builddepinfo
            'obs-service-recompress',
            'obs-service-set_version',
            'obs-service-tar_scm',
            # Used by ARM only, but part of oS:F ring 1 in general
            'u-boot',
            'raspberrypi-firmware-dt',
            'raspberrypi-firmware-config',
            # Added manually to notice failures early
            'vagrant',
            # https://github.com/openSUSE/open-build-service/issues/14129
            'snobol4',
            # https://github.com/os-autoinst/os-autoinst-distri-opensuse/pull/17637
            'docker-buildx',
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
                print('# {} not a link'.format(pkg))
            else:
                linked = links[0]
                dprj = linked.get('project')
                dpkg = linked.get('package')
                if dprj != self.api.project:
                    if not dprj.startswith(self.api.crings):
                        print("#{} not linking to base {} but {}".format(pkg, self.api.project, dprj))
                    self.links[pkg] = dpkg
                # multi spec package must link to ring
                elif len(links) > 1:
                    mainpkg = links[1].get('package')
                    mainprj = links[1].get('project')
                    if mainprj != self.api.project:
                        print('# FIXME: {} links to {}'.format(pkg, mainprj))
                    else:
                        destring = None
                        if mainpkg in self.api.ring_packages:
                            destring = self.api.ring_packages[mainpkg]
                        if not destring:
                            print('# {} links to {} but is not in a ring'.format(pkg, mainpkg))
                            print("osc linkpac {}/{} {}/{}".format(mainprj, mainpkg, prj, mainpkg))
                        else:
                            if pkg != 'glibc.i686':  # FIXME: ugly exception
                                print("osc linkpac -f {}/{} {}/{}".format(destring, mainpkg, prj, pkg))
                                self.links[pkg] = mainpkg

    def fill_pkginfo(self, prj, repo, arch):
        root = builddepinfo(self.api.apiurl, prj, repo, arch)

        for package in root.findall('package'):
            name = package.attrib['name']

            self.sources.add(name)

            for subpkg in package.findall('subpkg'):
                subpkg = subpkg.text
                if subpkg in self.bin2src:
                    if self.bin2src[subpkg] == name:
                        # different archs
                        continue
                    print('# Binary {} is defined twice: {} {}+{}'.format(subpkg, prj, name, self.bin2src[subpkg]))
                self.bin2src[subpkg] = name

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
                    print('Package {}/{}/{} is {}'.format(repo.get('project'),
                          repo.get('repository'), package.get('package'), code))
                    return False
        return True

    def check_image_bdeps(self, project, arch):
        url = makeurl(self.api.apiurl, ['build', project, '_result'])
        root = ET.parse(http_GET(url)).getroot()
        for image in root.xpath(f"result[@repository = 'images' and @arch = '{arch}']/status[@code != 'excluded' and @code != 'disabled']"):
            dvd = image.get('package')
            url = makeurl(self.api.apiurl, ['build', project, 'images', arch, dvd, '_buildinfo'])
            root = ET.parse(http_GET(url)).getroot()
            # Don't delete the image itself
            self.pkgdeps[dvd.split(':')[0]] = 'MYdvd{}'.format(self.api.rings.index(project))
            for bdep in root.findall('bdep'):
                if 'name' not in bdep.attrib:
                    continue
                b = bdep.attrib['name']
                if b not in self.bin2src:
                    print("{} not found in bin2src".format(b))
                    continue
                b = self.bin2src[b]
                self.pkgdeps[b] = 'MYdvd{}'.format(self.api.rings.index(project))

    def check_buildconfig(self, project):
        url = makeurl(self.api.apiurl, ['build', project, 'standard', '_buildconfig'])
        for line in http_GET(url).read().splitlines():
            line = line.decode('utf-8')
            if line.startswith('Preinstall:') or line.startswith('Support:'):
                for prein in line.split(':')[1].split():
                    if prein not in self.bin2src:
                        continue
                    b = self.bin2src[prein]
                    self.pkgdeps[b] = 'MYinstall'

    @memoize(session=True)
    def package_get_requiredby(self, project, package, repo, arch):
        "For a given package, return which source packages it provides runtime deps for."
        ret = set()
        for fileinfo in fileinfo_ext_all(self.api.apiurl, project, repo, arch, package):
            for requiredby in fileinfo.findall('provides_ext/requiredby[@name]'):
                ret.add(self.bin2src[requiredby.get('name')])

        return ret

    def check_depinfo_ring(self, prj, nextprj):
        if not self.repo_state_acceptable(prj):
            return False

        # Dict of linking package -> linked package
        self.links = {}
        self.find_inner_ring_links(prj)

        # Only loop through sources once from their origin ring to ensure single
        # step moving to allow check_requiredby() to see result in each ring.
        self.sources = set()
        all_needed_sources = set()

        # For each arch, collect needed source packages.
        # Prioritize x86_64.
        for arch in reversed(self.api.cstaging_archs):
            print(f"Arch {arch}")

            # Dict of needed source pkg -> reason why it's needed
            self.pkgdeps = {}
            # Note: bin2src is not cleared, that way ring1 pkgs can depend
            # on binaries from ring0.
            self.fill_pkginfo(prj, 'standard', arch)

            # 1. No images built, just for bootstrapping the rpm buildenv.
            # 2. Treat multibuild flavors as independent packages
            is_ring0 = self.api.rings.index(prj) == 0

            # Collect directly needed packages:
            # For ring 0, prjconf (Preinstall). For ring 1, images.
            if is_ring0:
                self.check_buildconfig(prj)
            else:
                self.check_image_bdeps(prj, arch)

            # Keep all preinstallimages
            for pkg in self.sources:
                if pkg.startswith("preinstallimage"):
                    self.pkgdeps[pkg] = "preinstallimage"

            # Treat all binaries in the whitelist as needed
            for pkg in self.whitelist:
                if pkg in self.sources:
                    self.pkgdeps[pkg] = "whitelist"

            to_visit = set(self.pkgdeps)
            # print("Directly needed: ", to_visit)

            url = makeurl(self.api.apiurl, ['build', prj, 'standard', arch, '_builddepinfo'], {"view": "pkgnames"})
            root = ET.parse(http_GET(url)).getroot()

            while len(to_visit) > 0:
                new_deps = {}
                for pkg in to_visit:
                    if not is_ring0:
                        # Outside of ring0, if one multibuild flavor is needed, add all of them
                        mainpkg = pkg.split(":")[0]
                        for src in self.sources:
                            if src.startswith(f"{mainpkg}:"):
                                new_deps[src] = pkg

                        # Same for link groups
                        for ldst, lsrc in self.links.items():
                            if lsrc == mainpkg:
                                new_deps[ldst] = pkg
                            elif ldst == mainpkg:
                                new_deps[lsrc] = pkg

                    # Add all packages which this package depends on
                    for dep in root.xpath(f"package[@name='{pkg}']/pkgdep"):
                        new_deps[dep.text] = pkg

                # Filter out already visited deps
                to_visit = set(new_deps).difference(set(self.pkgdeps))
                for pkg, reason in new_deps.items():
                    self.pkgdeps[pkg] = reason

                all_needed_sources |= set(self.pkgdeps)

                # _builddepinfo only takes care of build deps. runtime deps are handled by
                # fileinfo_ext_all, but that's really expensive. Thus the "obvious" algorithm
                # of walking from needed packages to their deps would be too slow. Instead,
                # walk from possibly unneeded packages (much fewer than needed) and check whether
                # they satisfy runtime deps of needed packages.
                # Do this after each batch of buildtime deps were resolved to minimize lookups.
                if len(to_visit) != 0:
                    continue

                # Technically this should be self.pkgdeps, but on i586 pretty much nothing
                # is needed (no built images) so we continue where x86_64 left off
                maybe_unneeded = self.sources.difference(all_needed_sources)
                for pkg in sorted(maybe_unneeded):
                    requiredby = self.package_get_requiredby(prj, pkg, 'standard', arch)
                    requiredby = requiredby.intersection(all_needed_sources)
                    # Required by needed packages?
                    if len(requiredby):
                        print(f"# {pkg} needed by {requiredby}")
                        # Include it and also resolve its build deps
                        self.pkgdeps[pkg] = requiredby
                        to_visit.add(pkg)

        self.commands.append(f"# For {prj}:")
        for source in sorted(self.sources):
            if source not in all_needed_sources:
                if ":" in source:
                    self.commands.append(f"# Multibuild flavor {source} not needed")
                else:
                    self.commands.append('osc rdelete -m cleanup {} {}'.format(prj, source))
                    if nextprj:
                        self.commands.append('osc linkpac {} {} {}'.format(self.api.project, source, nextprj))
