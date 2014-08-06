#!/usr/bin/python
# Copyright (c) 2014 SUSE Linux Products GmbH
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


from pprint import pprint
import os, sys, re
import logging
from optparse import OptionParser
import rpm
import pickle
import cmdln

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
        h = None
        try:
            h = self.ts.hdrFromFdno(fd)
        except rpm.error, e:
            if str(e) == "public key not available":
                print str(e)
            if str(e) == "public key not trusted":
                print str(e)
            if str(e) == "error reading package header":
                print str(e)
            h = None
        finally:
            os.close(fd)
        return h

    def readChangeLogs(self, args):

        pkgdata = dict()
        changelogs = dict()

        for path in args:
            for root, dirs, files in os.walk(path):
                for pkg in [ os.path.join(root, file) for file in files]:
                    h = self.readRpmHeader( pkg )
                    #print h.sprintf("[* %{CHANGELOGTIME:day} %{CHANGELOGNAME}\n%{CHANGELOGTEXT}\n\n]")
                    #print h['changelogname']
                    evr = dict()
                    for tag in ['name', 'version', 'release', 'sourcerpm']:
                        evr[tag] = h[tag]
                    pkgdata[h['name']] = evr

                    if h['sourcerpm'] in changelogs:
                        changelogs[h['sourcerpm']]['packages'].append(h['name'])
                    else:
                        data = { 'packages': [ h['name'] ] }
                        for tag in ['changelogtime', 'changelogtext']:
                            data[tag] = h[tag]
                        changelogs[h['sourcerpm']] = data
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
            raise Exception("%s must be a directory"%opts.dir)
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
            raise Exception("%s must be a directory"%opts.dir)

        f = open(os.path.join(opts.dir, version1), 'rb')
        (v, (v1pkgs, v1changelogs)) = pickle.load(f)
        if v != data_version:
            raise Exception("not matching version %s in %s"%(v, version1))
        f = open(os.path.join(opts.dir, version2), 'rb')
        (v, (v2pkgs, v2changelogs)) = pickle.load(f)
        if v != data_version:
            raise Exception("not matching version %s in %s"%(v, version2))

        p1 = set(v1pkgs.keys())
        p2 = set(v2pkgs.keys())

        print "Changed packages:"
        group = self._get_packages_grouped(v2pkgs, p1&p2)
#        pprint(p1&p2)
#        pprint(group)
#        print "  "+"\n  ".join(["\n   * ".join(sorted(group[s])) for s in sorted(group.keys()) ])
        for srpm in sorted(group.keys()):
            srpm1 = v1pkgs[group[srpm][0]]['sourcerpm']
            #print group[srpm], srpm, srpm1
            if srpm1 == srpm:
                continue # source package unchanged
            try:
                t1 = v1changelogs[srpm1]['changelogtime'][0]
            except IndexError:
                print >>sys.stderr, srpm1, "doesn't have a changelog"
                continue
            #print t1
            if t1 == v2changelogs[srpm]['changelogtime'][0]:
                continue # no new changelog entry, probably just rebuilt
            pkgs = sorted(group[srpm])
            print "\n==== %s ===="%pkgs[0]
            if v1pkgs[pkgs[0]]['version'] != v2pkgs[pkgs[0]]['version']:
                print "Version update (%s -> %s)"%(v1pkgs[pkgs[0]]['version'], v2pkgs[pkgs[0]]['version'])
            if len(pkgs) > 1:
                print "Subpackages:", " ".join(pkgs[1:])
            print
            for (i2, t2) in enumerate(v2changelogs[srpm]['changelogtime']):
                if t2 == t1:
                    break
                #print "+++ ",t2
                print v2changelogs[srpm]['changelogtext'][i2]

        print "Removed packages:"
        group = self._get_packages_grouped(v1pkgs, p1-p2)
        print "  "+"\n  ".join(["\n   > ".join(sorted(group[s])) for s in sorted(group.keys()) ])
        print "\n"
        print "Added packages:"
        group = self._get_packages_grouped(v2pkgs, p2-p1)
        print "  "+"\n  ".join(["\n   > ".join(sorted(group[s])) for s in sorted(group.keys()) ])

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

# vim: sw=4 et
