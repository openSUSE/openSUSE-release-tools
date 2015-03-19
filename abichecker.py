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
from stat import S_ISREG

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.conf
import osc.core
import urllib2
import rpm
from collections import namedtuple

import ReviewBot

class ABIChecker(ReviewBot.ReviewBot):
    """ check ABI of library packages
    """

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        self.ts = rpm.TransactionSet()
        self.ts.setVSFlags(rpm._RPMVSF_NOSIGNATURES)
    
    def check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package):
        ReviewBot.ReviewBot.check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package)
        
        if self._get_verifymd5(dst_project, dst_package) is None:
            self.logger.info("%s/%s does not exist, skip"%(dst_project, dst_package))
            return None

        # compute list of common repos
        myrepos = self.findrepos(src_project, dst_project)

        self.logger.debug(pformat(myrepos))

        notes = []

        # fetch cpio headers from source and target
        # check file lists for library packages

        missing_debuginfo = set()
        so_re = re.compile(r'^(?:/usr)/lib(?:64)?/[^/]+\.so(?:\.[^/]+)')
        debugpkg_re = re.compile(r'-debug(?:source|info)(?:-32bit)?$')
        for mr in myrepos:
            self.logger.debug('scanning %s/%s %s/%s'%(dst_project, dst_package, mr.dstrepo, mr.arch))
            headers = self._fetchcpioheaders(dst_project, dst_package, mr.dstrepo, mr.arch)
            lib_packages = dict() # pkgname -> set(lib file names)
            pkgs = dict() # pkgname -> rpmhdr
            for h in headers:
                pkgname = h['name']
                self.logger.debug(pkgname)
                pkgs[pkgname] = h
                if debugpkg_re.match(pkgname):
                    continue
                for fn, mode in zip(h['filenames'], h['filemodes']):
                    if so_re.match(fn) and S_ISREG(mode):
                        self.logger.debug('found lib: %s'%fn)
                        lib_packages.setdefault(pkgname, set()).add(fn)

            # check whether debug info exists for each lib
            for pkgname in sorted(lib_packages.keys()):
                # 32bit debug packages have special names
                if pkgname.endswith('-32bit'):
                    dpkgname = pkgname[:-len('-32bit')]+'-debuginfo-32bit'
                else:
                    dpkgname = pkgname+'-debuginfo'
                if not dpkgname in pkgs:
                    missing_debuginfo.add((dst_project, dst_package, mr.dstrepo, mr.arch, pkgname))
                    continue

                # check file list of debuginfo package
                h = pkgs[dpkgname]
                files = set (h['filenames'])
                for lib in lib_packages[pkgname]:
                    fn = '/usr/lib/debug%s.debug'%lib
                    if not fn in files:
                        missing_debuginfo.add((dst_project, dst_package, mr.dstrepo, mr.arch, pkgname, lib))

            if missing_debuginfo:
                self.logger.error('missing debuginfo: %s'%pformat(missing_debuginfo))
                return False

        # fetch binary rpms

        # extract binary rpms

        # run abichecker

        # upload result

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
        from osc.util.cpio import CpioRead

        u = osc.core.makeurl(self.apiurl, [ 'build', project, repo, arch, package ],
            [ 'view=cpioheaders' ])
        r = osc.core.http_GET(u)
        from tempfile import NamedTemporaryFile
        tmpfile = NamedTemporaryFile(prefix="cpio-", delete=False)
        for chunk in r:
            tmpfile.write(chunk)
        tmpfile.close()
        cpio = CpioRead(tmpfile.name)
        cpio.read()
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
                else:
                    yield h
        os.unlink(tmpfile.name)

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

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
