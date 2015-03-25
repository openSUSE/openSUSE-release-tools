#!/usr/bin/python
# Copyright (c) 2015 SUSE Linux GmbH
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

from pprint import pprint, pformat
import os, sys, re
import logging
from optparse import OptionParser
import cmdln
import re
from stat import S_ISREG, S_ISLNK
from tempfile import NamedTemporaryFile
import subprocess

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.conf
import osc.core
from osc.util.cpio import CpioRead

import urllib2
import rpm
from collections import namedtuple
from osclib.pkgcache import PkgCache

from xdg.BaseDirectory import save_cache_path

import ReviewBot

# Directory where download binary packages.
BINCACHE = os.path.expanduser('~/co')
DOWNLOADS = os.path.join(BINCACHE, 'downloads')

# Where the cache files are stored
CACHEDIR = save_cache_path('opensuse-abi-checker')

so_re = re.compile(r'^(?:/usr)/lib(?:64)?/lib([^/]+)\.so(?:\.[^/]+)?')
debugpkg_re = re.compile(r'-debug(?:source|info)(?:-32bit)?$')



class ABIChecker(ReviewBot.ReviewBot):
    """ check ABI of library packages
    """

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        self.ts = rpm.TransactionSet()
        self.ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)

        self.pkgcache = PkgCache(BINCACHE)

    def check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package):
        ReviewBot.ReviewBot.check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package)

        if self._get_verifymd5(dst_project, dst_package) is None:
            self.logger.info("%s/%s does not exist, skip"%(dst_project, dst_package))
            return None

        # compute list of common repos to find out what to compare
        myrepos = self.findrepos(src_project, dst_project)

        self.logger.debug(pformat(myrepos))

        notes = []

        for mr in myrepos:
            dst_libs = self.extract(dst_project, dst_package, mr.dstrepo, mr.arch)
            src_libs = self.extract(src_project, src_package, mr.srcrepo, mr.arch)

            # create reverse index for aliases in the source project
            src_aliases = dict()
            for lib in src_libs.keys():
                for a in src_libs[lib]:
                    src_aliases.setdefault(a, set()).add(lib)

            # for each library in the destination project check if the same lib
            # exists in the source project. If not check the aliases (symlinks)
            # to catch soname changes. Generate pairs of matching libraries.
            pairs = set()
            for lib in dst_libs.keys():
                if lib in src_libs:
                    paris.add((lib, lib))
                else:
                    self.logger.debug("%s not found in submission, checking aliases", lib)
                    for a in dst_libs[lib]:
                        if a in src_aliases:
                            for l in src_aliases[a]:
                                pairs.add((lib, l))

            self.logger.debug("to diff: %s", pformat(pairs))
            
            # for each pair dump and compare the abi
            for old, new in pairs:
                # abi dump of old lib
                new_base = os.path.join(CACHEDIR, 'unpacked', dst_project, dst_package, mr.dstrepo, mr.arch)
                old_dump = os.path.join(CACHEDIR, 'old.dump')
                # abi dump of new lib
                old_base = os.path.join(CACHEDIR, 'unpacked', src_project, src_package, mr.srcrepo, mr.arch)
                new_dump = os.path.join(CACHEDIR, 'new.dump')

                m = so_re.match(old)

                if m \
                    and self.run_abi_dumper(old_dump, new_base, old) \
                    and self.run_abi_dumper(new_dump, old_base, new):
                        report = os.path.join(CACHEDIR, 'report.html')
                        if self.run_abi_checker(m.group(1), old_dump, new_dump, report):
                            self.logger.info('report saved to %s', report)
                else:
                    self.logger.error('failed to compare %s <> %s'%(old,new))
                    # XXX: report

        # run abichecker
        # upload result

    def run_abi_checker(self, libname, old, new, output):
        cmd = ['abi-compliance-checker',
                '-lib', libname,
                '-old', old,
                '-new', new,
                '-report-path', output 
                ]
        self.logger.debug(cmd)
        r = subprocess.call(cmd, close_fds=True)
        if not r in (0, 1):
            self.logger.error('abi-compliance-checker failed')
            # XXX: record error
            return False
        return True

    def run_abi_dumper(self, output, base, filename):
        cmd = ['abi-dumper',
                '-o', output,
                '-lver', os.path.basename(filename),
                '-debuginfo-dir', '%s/usr/lib/debug/%s'%(base, os.path.dirname(filename)),
                '/'.join([base, filename])]
        self.logger.debug(cmd)
        r = subprocess.call(cmd, close_fds=True)
        if r != 0:
            self.logger.error("failed to dump %s!"%filename)
            # XXX: record error
            return False
        return True

    def extract(self, project, package, repo, arch):
            # fetch cpio headers
            # check file lists for library packages
            fetchlist, liblist = self.compute_fetchlist(project, package, repo, arch)

            if not fetchlist:
                self.logger.warning("fetchlist empty")
                # XXX record
                return

            # mtimes in cpio are not the original ones, so we need to fetch
            # that separately :-(
            mtimes= self._getmtimes(project, package, repo, arch)

            self.logger.debug("fetchlist %s", pformat(fetchlist))
            self.logger.debug("liblist %s", pformat(liblist))

            debugfiles = set(['/usr/lib/debug%s.debug'%f for f in liblist])

            # fetch binary rpms
            downloaded = self.download_files(project, package, repo, arch, fetchlist, mtimes)

            # extract binary rpms
            tmpfile = os.path.join(CACHEDIR, "cpio")
            for fn in fetchlist:
                self.logger.debug("extract %s"%fn)
                with open(tmpfile, 'wb') as tmpfd:
                    if not fn in downloaded:
                        self.logger.error("%s was not downloaded!"%fn)
                        # XXX: record error
                        continue
                    self.logger.debug(downloaded[fn])
                    r = subprocess.call(['rpm2cpio', downloaded[fn]], stdout=tmpfd, close_fds=True)
                    if r != 0:
                        self.logger.error("failed to extract %s!"%fn)
                        # XXX: record error
                        continue
                    tmpfd.close()
                    cpio = CpioRead(tmpfile)
                    cpio.read()
                    for ch in cpio:
                        fn = ch.filename
                        if fn.startswith('./'): # rpm payload is relative
                            fn = fn[1:]
                        self.logger.debug("cpio fn %s", fn)
                        if not fn in liblist and not fn in debugfiles:
                            continue
                        dst = os.path.join(CACHEDIR, 'unpacked', project, package, repo, arch)
                        dst += fn
                        if not os.path.exists(os.path.dirname(dst)):
                            os.makedirs(os.path.dirname(dst))
                        self.logger.debug("dst %s", dst)
                        # the filehandle in the cpio archive is private so
                        # open it again
                        with open(tmpfile, 'rb') as cpiofh:
                            cpiofh.seek(ch.dataoff, os.SEEK_SET)
                            with open(dst, 'wb') as fh:
                                while True:
                                    buf = cpiofh.read(4096)
                                    if buf is None or buf == '':
                                        break
                                    fh.write(buf)
            os.unlink(tmpfile)

            return liblist

    def download_files(self, project, package, repo, arch, filenames, mtimes):
        downloaded = dict()
        for fn in filenames:
            if not fn in mtimes:
                self.logger.error("missing mtime information for %s, can't check"% fn)
                # XXX record error
                continue
            repodir = os.path.join(DOWNLOADS, package, project, repo)
            if not os.path.exists(repodir):
                os.makedirs(repodir)
            t = os.path.join(repodir, fn)
            self._get_binary_file(project, repo, arch, package, fn, t, mtimes[fn])
            downloaded[fn] = t
        return downloaded

    # XXX: from repochecker
    def _get_binary_file(self, project, repository, arch, package, filename, target, mtime):
        """Get a binary file from OBS."""
        # Check if the file is already there.
        key = (project, repository, arch, package, filename, mtime)
        if key in self.pkgcache:
            try:
                os.unlink(target)
            except:
                pass
            self.pkgcache.linkto(key, target)
        else:
            osc.core.get_binary_file(self.apiurl, project, repository, arch,
                            filename, package=package,
                            target_filename=target)
            self.pkgcache[key] = target

    def readRpmHeaderFD(self, fd):
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
        return h

    def _fetchcpioheaders(self, project, package, repo, arch):
        u = osc.core.makeurl(self.apiurl, [ 'build', project, repo, arch, package ],
            [ 'view=cpioheaders' ])
        try:
            r = osc.core.http_GET(u)
        except urllib2.HTTPError, e:
            self.logger.error('failed to fetch header information')
            raise StopIteration
        tmpfile = NamedTemporaryFile(prefix="cpio-", delete=False)
        for chunk in r:
            tmpfile.write(chunk)
        tmpfile.close()
        cpio = CpioRead(tmpfile.name)
        cpio.read()
        rpm_re = re.compile('(.+\.rpm)-[0-9A-Fa-f]{32}$')
        for ch in cpio:
            # ignore errors
            if ch.filename == '.errors':
                continue
            # the filehandle in the cpio archive is private so
            # open it again
            with open(tmpfile.name, 'rb') as fh:
                fh.seek(ch.dataoff, os.SEEK_SET)
                h = self.readRpmHeaderFD(fh)
                if h is None:
                    self.logger.warn("failed to read rpm header for %s"%ch.filename)
                    continue
                m = rpm_re.match(ch.filename)
                if m:
                    yield m.group(1), h
        os.unlink(tmpfile.name)

    def _getmtimes(self, prj, pkg, repo, arch):
        """ returns a dict of filename: mtime """
        url = osc.core.makeurl(self.apiurl, ('build', prj, repo, arch, pkg))
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return None

        return dict([(node.attrib['filename'], node.attrib['mtime']) for node in root.findall('binary')])


    def findrepos(self, src_project, dst_project):
        url = osc.core.makeurl(self.apiurl, ('source', dst_project, '_meta'))
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return None

        # build list of target repos as set of name, arch
        dstrepos = set()
        for repo in root.findall('repository'):
            name = repo.attrib['name']
            for node in repo.findall('arch'):
                dstrepos.add((name, node.text))

        url = osc.core.makeurl(self.apiurl, ('source', src_project, '_meta'))
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return None

        MR = namedtuple('MatchRepo', ('srcrepo', 'dstrepo', 'arch'))
        # set of source repo name, target repo name, arch
        matchrepos = set()
        for repo in root.findall('repository'):
            name = repo.attrib['name']
            path = repo.findall('path')
            if path is None or len(path) != 1:
                continue
            prj = path[0].attrib['project']
            if prj == 'openSUSE:Tumbleweed':
                prj = 'openSUSE:Factory' # XXX: hack
            if prj != dst_project:
                continue
            for node in repo.findall('arch'):
                arch = node.text
                dstname = path[0].attrib['repository']
                if (dstname, arch) in dstrepos:
                    matchrepos.add(MR(name, dstname, arch))

        return matchrepos

    def compute_fetchlist(self, prj, pkg, repo, arch):
        self.logger.debug('scanning %s/%s %s/%s'%(prj, pkg, repo, arch))

        headers = self._fetchcpioheaders(prj, pkg, repo, arch)
        missing_debuginfo = set()
        lib_packages = dict() # pkgname -> set(lib file names)
        pkgs = dict() # pkgname -> cpiohdr, rpmhdr
        lib_aliases = dict()
        for rpmfn, h in headers:
            # skip src rpm
            if h['sourcepackage']:
                continue
            pkgname = h['name']
            self.logger.debug(pkgname)
            pkgs[pkgname] = (rpmfn, h)
            if debugpkg_re.match(pkgname):
                continue
            for fn, mode, lnk in zip(h['filenames'], h['filemodes'], h['filelinktos']):
                if so_re.match(fn):
                    if S_ISREG(mode):
                        self.logger.debug('found lib: %s'%fn)
                        lib_packages.setdefault(pkgname, set()).add(fn)
                    elif S_ISLNK(mode) and lnk is not None:
                        alias = os.path.basename(fn)
                        libname = os.path.basename(lnk)
                        self.logger.debug('found alias: %s -> %s'%(alias, libname))
                        lib_aliases.setdefault(libname, set()).add(alias)

        fetchlist = set()
        liblist = dict()
        # check whether debug info exists for each lib
        for pkgname in sorted(lib_packages.keys()):
            # 32bit debug packages have special names
            if pkgname.endswith('-32bit'):
                dpkgname = pkgname[:-len('-32bit')]+'-debuginfo-32bit'
            else:
                dpkgname = pkgname+'-debuginfo'
            if not dpkgname in pkgs:
                missing_debuginfo.add((prj, pkg, repo, arch, pkgname))
                continue

            # check file list of debuginfo package
            rpmfn, h = pkgs[dpkgname]
            files = set (h['filenames'])
            ok = True
            for lib in lib_packages[pkgname]:
                fn = '/usr/lib/debug%s.debug'%lib
                if not fn in files:
                    missing_debuginfo.add((prj, pkg, repo, arch, pkgname, lib))
                    ok = False
                if ok:
                    fetchlist.add(pkgs[pkgname][0])
                    fetchlist.add(rpmfn)
                    liblist.setdefault(lib, set())
                    libname = os.path.basename(lib)
                    if libname in lib_aliases:
                        liblist[lib] |= lib_aliases[libname]

        if missing_debuginfo:
            self.logger.error('missing debuginfo: %s'%pformat(missing_debuginfo))
            return None

        return fetchlist, liblist

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        if user is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        return ABIChecker(apiurl = apiurl, \
                dryrun = self.options.dry, \
                user = user, \
                logger = self.logger)

    @cmdln.option('-r', '--revision', metavar="number", type="int", help="revision number")
    def do_diff(self, subcmd, opts, src_project, src_package, dst_project, dst_package):
        src_rev = opts.revision
        print self.checker.check_source_submission(src_project, src_package, src_rev, dst_project, dst_package)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
