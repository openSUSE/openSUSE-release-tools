#!/usr/bin/python3

from pprint import pprint
import io
import os
import sys
import logging
from optparse import OptionParser
import rpm
import pickle
import cmdln
import re

SRPM_RE = re.compile(
    r'(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<suffix>(?:no)?src\.rpm)$')

data_version = 3

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

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

    def readChangeLogs(self, args):

        pkgdata = dict()
        changelogs = dict()

        def _getdata(h):
            srpm = str(h['sourcerpm'], 'utf-8')
            binrpm = str(h['name'], 'utf-8')

            evr = dict()
            for tag in ['name', 'version', 'release', 'sourcerpm']:
                evr[tag] = str(h[tag], 'utf-8')
            pkgdata[binrpm] = evr

            # dirty hack to reduce kernel spam
            m = SRPM_RE.match(srpm)
            if m and m.group('name') in (
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
                srpm = '%s-%s-%s.src.rpm' % ('kernel-source', m.group('version'), m.group('release'))
                pkgdata[binrpm]['sourcerpm'] = srpm
                print("%s -> %s" % (str(h['sourcerpm'], 'utf-8'), srpm))

            if srpm in changelogs:
                changelogs[srpm]['packages'].append(binrpm)
            else:
                data = { 'packages': [ binrpm ] }
                data['changelogtime'] = h['changelogtime']
                data['changelogtext'] = h['changelogtext']
                for (t, txt) in enumerate(data['changelogtext']):
                    data['changelogtext'][t] = str(txt, 'utf-8')
                changelogs[srpm] = data

        for arg in args:
            if arg.endswith('.iso'):
                import pycdio
                import iso9660
                iso = iso9660.ISO9660.IFS(source=arg)
                fd = os.open(arg, os.O_RDONLY)

                if not iso.is_open() or fd is None:
                    raise Exception("Could not open %s as an ISO-9660 image." % arg)

                # On Tumbleweed, there is no '/suse' prefix
                for path in ['/suse/x86_64', '/suse/noarch', '/suse/aarch64',
                             '/suse/s390x', '/x86_64', '/noarch', '/aarch64', '/s390x' ]:
                    file_stats = iso.readdir(path)
                    if file_stats is None:
                        continue

                    for stat in file_stats:
                        filename = stat[0]
                        LSN = stat[1]
                        size = stat[2]
                        sec_size = stat[3]
                        is_dir = stat[4] == 2
#                       print("%s [LSN %6d] %8d %s%s" % (dir_tr[is_dir], LSN, size, path,
#                           iso9660.name_translate(filename)))

                        if (filename.endswith('.rpm')):
                            os.lseek(fd, LSN * pycdio.ISO_BLOCKSIZE, io.SEEK_SET)
                            h = self.ts.hdrFromFdno(fd)
                            _getdata(h)

                os.close(fd)

            elif os.path.isdir(arg):
                for root, dirs, files in os.walk(arg):
                    for pkg in [ os.path.join(root, file) for file in files]:
                        if not pkg.endswith('.rpm'):
                            continue
                        h = self.readRpmHeader( pkg )
                        _getdata(h)
            else:
                raise Exception("don't know what to do with %s" % arg)

        return pkgdata, changelogs

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
            raise Exception("%s must be a directory" % opts.dir)
        if not opts.snapshot:
            raise Exception("missing snapshot option")

        f = open(os.path.join(opts.dir, opts.snapshot), 'wb')
        pickle.dump([data_version, self.readChangeLogs(dirs)], f)

    def do_dump(self, subcmd, opts, *dirs):
        """${cmd_name}: pprint the package changelog information

        ${cmd_usage}
        ${cmd_option_list}
        """
        pprint(self.readChangeLogs(dirs))

    def do_inspect(self, subcmd, opts, filename, package):
        """${cmd_name}: pprint the package changelog information

        ${cmd_usage}
        ${cmd_option_list}
        """
        f = open(filename, 'rb')
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
            raise Exception("%s must be a directory" % opts.dir)

        f = open(os.path.join(opts.dir, version1), 'rb')
        (v, (v1pkgs, v1changelogs)) = pickle.load(f,
            encoding='utf-8', errors='backslashreplace')
        if v != data_version:
            raise Exception("not matching version %s in %s" % (v, version1))
        f = open(os.path.join(opts.dir, version2), 'rb')
        (v, (v2pkgs, v2changelogs)) = pickle.load(f,
            encoding='utf-8', errors='backslashreplace')
        if v != data_version:
            raise Exception("not matching version %s in %s" % (v, version2))

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
                continue # source package unchanged
            try:
                t1 = v1changelogs[srpm1]['changelogtime'][0]
            except IndexError:
                print("{} doesn't have a changelog".format(srpm1), file=sys.stderr)
                continue
            m = SRPM_RE.match(srpm)
            if m:
                name = m.group('name')
            else:
                name = srpm
            if len(v2changelogs[srpm]['changelogtime']) == 0:
                print('  {} ERROR: no changelog'.format(name))
                continue
            if t1 == v2changelogs[srpm]['changelogtime'][0]:
                continue # no new changelog entry, probably just rebuilt
            pkgs = sorted(group[srpm])
            details += "\n==== %s ====\n" % name
            if v1pkgs[pkgs[0]]['version'] != v2pkgs[pkgs[0]]['version']:
                print("  %s (%s -> %s)" % (name, v1pkgs[pkgs[0]]['version'],
                                           v2pkgs[pkgs[0]]['version']))
                details += "Version update (%s -> %s)\n" % (v1pkgs[pkgs[0]]['version'],
                                                            v2pkgs[pkgs[0]]['version'])
            else:
                print("  %s" % name)
            if len(pkgs) > 1:
                details += "Subpackages: %s\n" % " ".join([p for p in pkgs if p != name])
            for (i2, t2) in enumerate(v2changelogs[srpm]['changelogtime']):
                if t2 <= t1:
                    break
                details += "\n" + v2changelogs[srpm]['changelogtext'][i2]
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
    sys.exit( app.main() )
