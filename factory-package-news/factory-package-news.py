#!/usr/bin/python3

import cmdln
import gzip
import io
import logging
import os
import pickle
from pprint import pprint
import pyzstd
import re
import rpm
import sys
import xml.etree.ElementTree as ET

SRPM_RE = re.compile(
    r'(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<suffix>(?:no)?src\.rpm)$')
TEXLIVE_SPECS_RE = re.compile(r'^texlive-specs-[a-z]$')
CROSS_GCC_RE = re.compile(r'^cross-.+-gcc(?P<ver>\d+)(?:-bootstrap)?$')
CROSS_BINUTILS_RE = re.compile(r'^cross-.+-binutils$')
GLIBC_CROSS_RE = re.compile(r'^glibc-cross-.+-src$')
CROSS_NEWLIB_RE = re.compile(r'^cross-.+-newlib-devel$')

KERNEL_VARIANTS = (
    'kernel-64kb',
    'kernel-debug',
    'kernel-default',
    'kernel-desktop',
    'kernel-docs',
    'kernel-ec2',
    'kernel-kvmsmall',
    'kernel-lpae',
    'kernel-obs-build',
    'kernel-obs-qa-xen',
    'kernel-obs-qa',
    'kernel-pae',
    'kernel-pv',
    'kernel-syms',
    'kernel-vanilla',
    'kernel-xen',
)

COMMON_NS = 'http://linux.duke.edu/metadata/common'
OTHER_NS = 'http://linux.duke.edu/metadata/other'
RPM_NS = 'http://linux.duke.edu/metadata/rpm'

data_version = 3

changelog_max_lines = 30  # maximum number of changelog lines per package


# rpm's python bindings changed in version 4.15 [0] so that they actually return
# utf-8 strings. Leap 15 ships an older version, so this is needed to
# keep this script working there too.
#
# [0] https://github.com/rpm-software-management/rpm/commit/84920f898315d09a57a3f1067433eaeb7de5e830
def utf8str(content):
    return str(content, 'utf-8') if isinstance(content, bytes) else content


def normalize_srpm(srpm):
    m = SRPM_RE.match(srpm)
    if not m:
        return srpm

    name = m.group('name')

    # dirty hack to reduce kernel spam
    if name.startswith('kernel-') and name.endswith('-longterm') and name != 'kernel-source-longterm':
        new_srpm = f"kernel-source-longterm-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    if name in KERNEL_VARIANTS:
        new_srpm = f"kernel-source-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    # dirty hack to reduce texlive spam (texlive-specs-a .. texlive-specs-z)
    if TEXLIVE_SPECS_RE.match(name):
        new_srpm = f"texlive-specs-{m.group('version')}-1.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    # dirty hack to reduce cross toolchain spam
    m_cross_gcc = CROSS_GCC_RE.match(name)
    if m_cross_gcc:
        new_srpm = f"gcc{m_cross_gcc.group('ver')}-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    if CROSS_BINUTILS_RE.match(name):
        new_srpm = f"binutils-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    if GLIBC_CROSS_RE.match(name):
        new_srpm = f"glibc-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    if CROSS_NEWLIB_RE.match(name):
        new_srpm = f"cross-newlib-devel-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    return srpm


def open_compressed(path):
    """Open repodata file with transparent decompression (.zst or .gz)."""
    if path.endswith('.zst'):
        return pyzstd.ZstdFile(path, 'rb')
    elif path.endswith('.gz'):
        return gzip.open(path, 'rb')
    return open(path, 'rb')


class ChangeLogger(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)
        self.ts = rpm.TransactionSet()
        self.ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)

    def readRpmHeader(self, filename):
        """ Read an rpm header. """
        fd = os.open(filename, os.O_RDONLY)
        h = self.readRpmHeaderFD(fd)
        os.close(fd)
        return h

    def readRpmHeaderFD(self, fd):
        h = None
        try:
            h = self.ts.hdrFromFdno(fd)
        except rpm.error as e:
            if str(e) == "public key not available":
                print(str(e))
            if str(e) == "public key not trusted":
                print(str(e))
            if str(e) == "error reading package header":
                print(str(e))
            h = None
        return h

    def read_repomd(self, repo_dir, arch=None):
        repomd_path = os.path.join(repo_dir, 'repodata', 'repomd.xml')
        with open_compressed(repomd_path) as stream:
            tree = ET.parse(stream)

        pri_href = tree.find("./{*}data[@type='primary']/{*}location").attrib['href']
        oth_href = tree.find("./{*}data[@type='other']/{*}location").attrib['href']

        pkgdata = dict()
        pkgid_to_srpm = dict()
        srpm_to_packages = dict()

        with open_compressed(os.path.join(repo_dir, pri_href)) as pri_stream:
            context = ET.iterparse(pri_stream, events=('start', 'end'))
            _, root = next(iter(context))
            for event, elem in context:
                if event == 'end' and elem.tag == f'{{{COMMON_NS}}}package':
                    name = elem.findtext(f'{{{COMMON_NS}}}name')
                    pkg_arch = elem.findtext(f'{{{COMMON_NS}}}arch')
                    ver_elem = elem.find(f'{{{COMMON_NS}}}version')
                    srpm = elem.findtext(f'.//{{{RPM_NS}}}sourcerpm') or elem.findtext('.//{*}sourcerpm')
                    pkgid = elem.findtext(f"./{{{COMMON_NS}}}checksum[@pkgid='YES']")

                    if pkg_arch not in ('src', 'nosrc') and name and srpm:
                        if arch is None or pkg_arch in (arch, 'noarch'):
                            srpm = normalize_srpm(srpm)
                            pkgdata[name] = {
                                'name': name,
                                'version': ver_elem.attrib.get('ver', '') if ver_elem is not None else '',
                                'release': ver_elem.attrib.get('rel', '') if ver_elem is not None else '',
                                'sourcerpm': srpm,
                            }
                            if pkgid:
                                pkgid_to_srpm[pkgid] = srpm
                            srpm_to_packages.setdefault(srpm, set()).add(name)
                    root.clear()

        changelogs = dict()
        with open_compressed(os.path.join(repo_dir, oth_href)) as oth_stream:
            context = ET.iterparse(oth_stream, events=('start', 'end'))
            _, root = next(iter(context))
            for event, elem in context:
                if event == 'end' and elem.tag == f'{{{OTHER_NS}}}package':
                    pkgid = elem.attrib.get('pkgid')
                    srpm = pkgid_to_srpm.get(pkgid)
                    # None for packages of other architectures or src packages filtered during primary.xml parsing
                    if not srpm:
                        root.clear()
                        continue
                    if srpm not in changelogs:
                        entries = [
                            (int(c.attrib.get('date', 0)), utf8str(c.text or ''))
                            for c in elem.findall(f'{{{OTHER_NS}}}changelog')
                        ]
                        entries.sort(key=lambda x: x[0], reverse=True)
                        changelogs[srpm] = {
                            'packages': set(srpm_to_packages.get(srpm, ())),
                            'changelogtime': [d for d, _ in entries],
                            'changelogtext': [t for _, t in entries],
                        }
                    root.clear()

        for srpm, pkgs in srpm_to_packages.items():
            changelogs.setdefault(srpm, {'packages': set(pkgs), 'changelogtime': [], 'changelogtext': []})['packages'] = set(pkgs)

        return pkgdata, changelogs

    def readChangeLogs(self, args, arch=None):

        pkgdata = dict()
        changelogs = dict()

        def _getdata(h):
            srpm = utf8str(h['sourcerpm'])
            binrpm = utf8str(h['name'])

            evr = dict()
            for tag in ['name', 'version', 'release', 'sourcerpm']:
                evr[tag] = utf8str(h[tag])
            pkgdata[binrpm] = evr

            srpm = normalize_srpm(srpm)
            pkgdata[binrpm]['sourcerpm'] = srpm

            if srpm in changelogs:
                changelogs[srpm]['packages'].add(binrpm)
            else:
                data = {'packages': {binrpm}}
                data['changelogtime'] = h['changelogtime']
                data['changelogtext'] = h['changelogtext']
                for (t, txt) in enumerate(data['changelogtext']):
                    data['changelogtext'][t] = utf8str(txt)
                changelogs[srpm] = data

        def _walk_through_iso_image(iso, path="/"):
            file_stats = iso.readdir(path)
            if file_stats is None:
                raise Exception(f"Unable to find directory {path} inside the iso image")

            for stat in file_stats:
                filename = stat[0]
                LSN = stat[1]
                is_directory = (stat[4] == 2)  # 2 --> directory

                if path == "/boot" or filename in ['.', '..']:
                    continue
                elif is_directory:
                    yield from _walk_through_iso_image(iso, path=os.path.join(path, filename))
                elif filename.endswith('.rpm'):
                    yield filename, LSN

            return None

        for arg in args:
            if arg.endswith('.iso'):
                import pycdio
                import iso9660
                iso = iso9660.ISO9660.IFS(source=arg)
                fd = os.open(arg, os.O_RDONLY)

                if not iso.is_open() or fd is None:
                    raise Exception(f"Could not open {arg} as an ISO-9660 image.")

                for filename, LSN in _walk_through_iso_image(iso):
                    os.lseek(fd, LSN * pycdio.ISO_BLOCKSIZE, io.SEEK_SET)
                    h = self.ts.hdrFromFdno(fd)
                    _getdata(h)

                os.close(fd)

            elif os.path.isdir(arg):
                r_pkgdata, r_changelogs = self.read_repomd(arg, arch=arch)
                pkgdata.update(r_pkgdata)
                for srpm, data in r_changelogs.items():
                    if srpm in changelogs:
                        changelogs[srpm]['packages'].update(data['packages'])
                    else:
                        changelogs[srpm] = data
            else:
                raise Exception(f"don't know what to do with {arg}")

        return pkgdata, changelogs

    @cmdln.option("--arch", action="store", type='string', dest='arch', default=None,
                  help="target architecture (optional, e.g. x86_64, aarch64)")
    @cmdln.option("--snapshot", action="store", type='string', help="snapshot number")
    @cmdln.option("--dir", action="store", type='string', dest='dir', help="data directory")
    def do_save(self, subcmd, opts, *dirs):
        """${cmd_name}: save changelog information for snapshot

        ${cmd_usage}
        ${cmd_option_list}
        """

        if not opts.dir:
            raise Exception("need --dir option")
        if not os.path.isdir(opts.dir):
            raise Exception(f"{opts.dir} must be a directory")
        if not opts.snapshot:
            raise Exception("missing snapshot option")

        with open(os.path.join(opts.dir, opts.snapshot), 'wb') as f:
            pickle.dump([data_version, self.readChangeLogs(dirs, arch=opts.arch)], f)

    @cmdln.option("--arch", action="store", type='string', dest='arch', default=None,
                  help="target architecture (optional, e.g. x86_64, aarch64)")
    def do_dump(self, subcmd, opts, *dirs):
        """${cmd_name}: pprint the package changelog information

        ${cmd_usage}
        ${cmd_option_list}
        """
        pprint(self.readChangeLogs(dirs, arch=opts.arch))

    def do_inspect(self, subcmd, opts, filename, package):
        """${cmd_name}: pprint the package changelog information

        ${cmd_usage}
        ${cmd_option_list}
        """
        with open(filename, 'rb') as f:
            (v, (pkgs, changelogs)) = pickle.load(
                f, encoding='utf-8', errors='backslashreplace')
        pprint(pkgs[package])
        pprint(changelogs[pkgs[package]['sourcerpm']])

    def _get_packages_grouped(self, pkgs, names):
        group = dict()
        for pkg in names:
            if not pkgs[pkg]['sourcerpm'] in group:
                group[pkgs[pkg]['sourcerpm']] = [pkg]
            else:
                group[pkgs[pkg]['sourcerpm']].append(pkg)
        return group

    @cmdln.option("--dir", action="store", type='string', dest='dir', help="data directory")
    def do_diff(self, subcmd, opts, version1, version2):
        """${cmd_name}: diff two snapshots

        ${cmd_usage}
        ${cmd_option_list}
        """
        if not opts.dir:
            raise Exception("need --dir option")
        if not os.path.isdir(opts.dir):
            raise Exception(f"{opts.dir} must be a directory")

        with open(os.path.join(opts.dir, version1), 'rb') as f:
            (v, (v1pkgs, v1changelogs)) = pickle.load(f,
                                                      encoding='utf-8', errors='backslashreplace')
        if v != data_version:
            raise Exception(f"not matching version {v} in {version1}")
        with open(os.path.join(opts.dir, version2), 'rb') as f:
            (v, (v2pkgs, v2changelogs)) = pickle.load(f,
                                                      encoding='utf-8', errors='backslashreplace')
        if v != data_version:
            raise Exception(f"not matching version {v} in {version2}")

        p1 = set(v1pkgs.keys())
        p2 = set(v2pkgs.keys())

        print('Packages changed:')
        group = self._get_packages_grouped(v2pkgs, p1 & p2)
#        pprint(p1&p2)
#        pprint(group)
#        print "  "+"\n  ".join(["\n   * ".join(sorted(group[s])) for s in sorted(group.keys()) ])
        details = ''
        for srpm in sorted(group.keys()):
            srpm1 = v1pkgs[group[srpm][0]]['sourcerpm']
            # print group[srpm], srpm, srpm1
            if srpm1 == srpm:
                continue  # source package unchanged
            try:
                t1 = v1changelogs[srpm1]['changelogtime'][0]
            except IndexError:
                print(f"{srpm1} doesn't have a changelog", file=sys.stderr)
                continue
            m = SRPM_RE.match(srpm)
            if m:
                name = m.group('name')
            else:
                name = srpm
            if len(v2changelogs[srpm]['changelogtime']) == 0:
                print(f'  {name} ERROR: no changelog')
                continue
            if t1 == v2changelogs[srpm]['changelogtime'][0]:
                continue  # no new changelog entry, probably just rebuilt
            pkgs = sorted(group[srpm])
            details += f"\n==== {name} ====\n"
            if v1pkgs[pkgs[0]]['version'] != v2pkgs[pkgs[0]]['version']:
                print("  %s (%s -> %s)" % (name, v1pkgs[pkgs[0]]['version'],
                                           v2pkgs[pkgs[0]]['version']))
                details += "Version update (%s -> %s)\n" % (v1pkgs[pkgs[0]]['version'],
                                                            v2pkgs[pkgs[0]]['version'])
            else:
                print(f"  {name}")
            if len(pkgs) > 1:
                subpkgs = [p for p in pkgs if p != name]
                if len(subpkgs) > 25:
                    details += f"Subpackages (showing 25 of {len(subpkgs)}): {' '.join(subpkgs[:25])} ...\n"
                else:
                    details += f"Subpackages: {' '.join(subpkgs)}\n"

            changedetails = ""
            for (i2, t2) in enumerate(v2changelogs[srpm]['changelogtime']):
                if t2 <= t1:
                    break
                changedetails += "\n" + v2changelogs[srpm]['changelogtext'][i2]

            # if a changelog is too long, cut it off after changelog_max_lines lines
            changedetails_lines = changedetails.splitlines()
            # apply 5 lines tolerance to avoid silly-looking "skipping 2 lines"
            if len(changedetails_lines) > changelog_max_lines + 5:
                changedetails = '\n'.join(changedetails_lines[0:changelog_max_lines])
                left = len(changedetails_lines) - changelog_max_lines - 1
                changedetails += f'\n    ... changelog too long, skipping {left} lines ...\n'
                # add last line of changelog diff so that it's possible to
                # find out the end of the changelog section
                changedetails += changedetails_lines[-1]
            details += changedetails
            details += '\n'

        print("\n=== Details ===")
        print(details)

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        return parser

    def postoptparse(self):
        logging.basicConfig()
        self.logger = logging.getLogger("factory-package-news")
        if (self.options.debug):
            self.logger.setLevel(logging.DEBUG)
        elif (self.options.verbose):
            self.logger.setLevel(logging.INFO)


if __name__ == "__main__":
    app = ChangeLogger()
    sys.exit(app.main())
