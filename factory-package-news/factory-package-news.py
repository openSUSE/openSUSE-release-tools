#!/usr/bin/python3

import cmdln
import gzip
import io
import logging
import os
import pickle
from pprint import pprint
import re
import rpm
import sys
import xml.etree.ElementTree as ET
import zstandard

SRPM_RE = re.compile(
    r'(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<suffix>(?:no)?src\.rpm)$')

data_version = 3

changelog_max_lines = 100  # maximum number of changelog lines per package


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
    if name in (
        'kernel-64kb',
        'kernel-debug',
        'kernel-default',
        'kernel-desktop',
        'kernel-docs',
        'kernel-ec2',
        'kernel-lpae',
        'kernel-obs-build',
        'kernel-obs-qa-xen',
        'kernel-obs-qa',
        'kernel-pae',
        'kernel-pv',
        'kernel-syms',
        'kernel-vanilla',
        'kernel-xen',
    ):
        new_srpm = f"kernel-source-{m.group('version')}-{m.group('release')}.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    # dirty hack to reduce texlive spam (texlive-specs-a .. texlive-specs-z)
    if re.match(r'^texlive-specs-[a-z]$', name):
        new_srpm = f"texlive-specs-{m.group('version')}-1.src.rpm"
        print(f"{srpm} -> {new_srpm}")
        return new_srpm

    return srpm


def open_compressed(path):
    """Open repodata file with transparent decompression (.zst or .gz)."""
    if path.endswith('.zst'):
        raw = open(path, 'rb')
        return zstandard.ZstdDecompressor().stream_reader(raw, closefd=True)
    elif path.endswith('.gz'):
        return gzip.open(path, 'rb')
    return open(path, 'rb')


def find_repomd(arg):
    """Find repomd.xml and repo base directory."""
    if os.path.isfile(arg) and arg.endswith('repomd.xml'):
        return arg, os.path.dirname(os.path.dirname(os.path.abspath(arg)))
    if os.path.isdir(arg):
        candidate = os.path.join(arg, 'repodata', 'repomd.xml')
        if os.path.isfile(candidate):
            return candidate, os.path.abspath(arg)
        candidate = os.path.join(arg, 'repomd.xml')
        if os.path.isfile(candidate):
            parent = os.path.dirname(os.path.abspath(arg))
            base = parent if os.path.basename(os.path.abspath(arg)) == 'repodata' else os.path.abspath(arg)
            return candidate, base
    return None


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

    def read_repomd(self, repomd_source, base, arch=None):
        with open_compressed(repomd_source) as stream:
            tree = ET.parse(stream)

        pri_href = tree.find("./{*}data[@type='primary']/{*}location").attrib['href']
        oth_href = tree.find("./{*}data[@type='other']/{*}location").attrib['href']

        pkgdata = dict()
        pkgid_to_srpm = dict()
        srpm_to_packages = dict()

        with open_compressed(os.path.join(base, pri_href)) as pri_stream:
            context = ET.iterparse(pri_stream, events=('start', 'end'))
            _, root = next(iter(context))
            for event, elem in context:
                if event == 'end' and elem.tag.endswith('package'):
                    name = elem.findtext('{*}name')
                    pkg_arch = elem.findtext('{*}arch')
                    ver_elem = elem.find('{*}version')
                    srpm = elem.findtext('.//{*}sourcerpm')
                    pkgid = elem.findtext("./{*}checksum[@pkgid='YES']")

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
                            srpm_to_packages.setdefault(srpm, []).append(name)
                    root.clear()

        changelogs = dict()
        with open_compressed(os.path.join(base, oth_href)) as oth_stream:
            context = ET.iterparse(oth_stream, events=('start', 'end'))
            _, root = next(iter(context))
            for event, elem in context:
                if event == 'end' and elem.tag.endswith('package'):
                    pkgid = elem.attrib.get('pkgid')
                    srpm = pkgid_to_srpm.get(pkgid)
                    if srpm and srpm not in changelogs:
                        entries = [
                            (int(c.attrib.get('date', 0)), utf8str(c.text or ''))
                            for c in elem if c.tag.endswith('changelog')
                        ]
                        entries.sort(key=lambda x: x[0], reverse=True)
                        changelogs[srpm] = {
                            'packages': srpm_to_packages.get(srpm, []),
                            'changelogtime': [d for d, _ in entries],
                            'changelogtext': [t for _, t in entries],
                        }
                    root.clear()

        for srpm, pkgs in srpm_to_packages.items():
            changelogs.setdefault(srpm, {'packages': pkgs, 'changelogtime': [], 'changelogtext': []})['packages'] = pkgs

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
                if binrpm not in changelogs[srpm]['packages']:
                    changelogs[srpm]['packages'].append(binrpm)
            else:
                data = {'packages': [binrpm]}
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
            repomd_info = find_repomd(arg)
            if repomd_info:
                repomd_file, base_dir = repomd_info
                r_pkgdata, r_changelogs = self.read_repomd(repomd_file, base_dir, arch=arch)
                pkgdata.update(r_pkgdata)
                for srpm, data in r_changelogs.items():
                    if srpm in changelogs:
                        for p in data['packages']:
                            if p not in changelogs[srpm]['packages']:
                                changelogs[srpm]['packages'].append(p)
                    else:
                        changelogs[srpm] = data
            elif arg.endswith('.iso'):
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
                for root, dirs, files in os.walk(arg):
                    for pkg in [os.path.join(root, file) for file in files]:
                        if not pkg.endswith('.rpm'):
                            continue
                        h = self.readRpmHeader(pkg)
                        _getdata(h)
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
