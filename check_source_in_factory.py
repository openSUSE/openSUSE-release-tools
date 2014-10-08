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
import cmdln

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.conf
import osc.core
import urllib2
import ReviewBot

class FactorySourceChecker(ReviewBot.ReviewBot):
    """ this review bot checks if the sources of a submission are
    either in Factory or a request for Factory with the same sources
    exist. If the latter a request is only accepted if the Factory
    request is reviewed positive."""

    def __init__(self, *args, **kwargs):
        self.factory = None
        if 'factory' in kwargs:
            self.factory = kwargs['factory']
            del kwargs['factory']
        if self.factory is None:
            self.factory = "openSUSE:Factory"
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.review_messages = { 'accepted' : 'ok', 'declined': 'the package needs to be accepted in Factory first' }

    def check_action_maintenance_incident(self, req, a):
        rev = self._get_verifymd5(a.src_project, a.src_package, a.src_rev)
        return self._check_package(a.src_project, a.src_package, rev, a.tgt_releaseproject, a.src_package)

    def check_action_maintenance_release(self, req, a):
        pkgname = a.src_package
        if pkgname == 'patchinfo':
            return None
        linkpkg = self._get_linktarget_self(a.src_project, pkgname)
        if linkpkg is not None:
            pkgname = linkpkg
        # packages in maintenance have links to the target. Use that
        # to find the real package name
        (linkprj, linkpkg) = self._get_linktarget(a.src_project, pkgname)
        if linkpkg is None or linkprj is None or linkprj != a.tgt_project:
            self.logger.error("%s/%s is not a link to %s"%(a.src_project, pkgname, a.tgt_project))
            return False
        else:
            pkgname = linkpkg
        src_rev = self._get_verifymd5(a.src_project, a.src_package)
        return self._check_package(a.src_project, a.src_package, src_rev, a.tgt_project, pkgname)

    def check_action_submit(self, req, a):
        rev = self._get_verifymd5(a.src_project, a.src_package, a.src_rev)
        return self._check_package(a.src_project, a.src_package, rev, a.tgt_package, a.tgt_package)

    def _check_package(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s"%(src_project, src_package, src_rev, target_project, target_package))
        good = self._check_factory(src_rev, target_package)

        if good:
            self.logger.info("%s is in Factory"%target_package)
            return good

        good = self._check_requests(src_rev, target_package)
        if good:
            self.logger.info("%s already reviewed for Factory"%target_package)

        return good

    def _check_factory(self, rev, package):
        """check if factory sources contain the package and revision. check head and history"""
        self.logger.debug("checking %s in %s"%(package, self.factory))
        srcmd5 = self._get_verifymd5(self.factory, package)
        if srcmd5 is None:
            self.logger.debug("new package")
            return None
        elif rev == srcmd5:
            self.logger.debug("srcmd5 matches")
            return True

        self.logger.debug("%s not the latest version, checking history", rev)
        u = osc.core.makeurl(self.apiurl, [ 'source', self.factory, package, '_history' ], { 'limit': '5' })
        try:
            r = osc.core.http_GET(u)
        except urllib2.HTTPError, e:
            self.logger.debug("package has no history!?")
            return None

        root = ET.parse(r).getroot()
        for revision in root.findall('revision'):
            node = revision.find('srcmd5')
            if node is None:
                continue
            self.logger.debug("checking %s"%node.text)
            if node.text == rev:
                self.logger.debug("got it, rev %s"%revision.get('rev'))
                return True

        self.logger.debug("srcmd5 not found in history either")
        return False

    def _check_requests(self, rev, package):
        self.logger.debug("checking requests")
        requests = osc.core.get_request_list(self.apiurl, self.factory, package, None, ['new', 'review'], 'submit')
        for req in requests:
            for a in req.actions:
                rqrev = self._get_verifymd5(a.src_project, a.src_package, a.src_rev)
                self.logger.debug("rq %s: %s/%s@%s"%(req.reqid, a.src_project, a.src_package, rqrev))
                if rqrev == rev:
                    if req.state.name == 'new':
                        self.logger.debug("request ok")
                        return True
                    elif req.state.name == 'review':
                        self.logger.debug("request still in review")
                        return None
                    else:
                        self.logger.error("request in state %s not expected"%req.state.name)
                        return None
        return False

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)
        parser.add_option("--factory", metavar="project", help="the openSUSE Factory project")

        return parser

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        if user is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        return FactorySourceChecker(apiurl = apiurl, \
                factory = self.options.factory, \
                dryrun = self.options.dry, \
                user = user, \
                logger = self.logger)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
