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

class Checker(object):
    requests = []

    def __init__(self, apiurl = None, factory = None, dryrun = False, logger = None):
        self.apiurl = apiurl
        self.factory = factory
        self.dryrun = dryrun
        self.logger = logger

        if self.factory is None:
            self.factory = "openSUSE:Factory"

    def set_request_ids(self, ids):
        for rqid in ids:
            u = osc.core.makeurl(self.apiurl, [ 'request', rqid ])
            r = osc.core.http_GET(u)
            root = ET.parse(r).getroot()
            req = osc.core.Request()
            req.read(root)
            self.requests.append(req)

    def check_requests(self):
        for req in self.requests:
            self._check_one_request(req)

    def _check_one_request(self, req):
        for a in req.actions:
            if a.type == 'maintenance_incident':
                self._check_package(a.src_project, a.src_package, a.src_rev, a.tgt_releaseproject, a.src_package)
            elif a.type == 'submit':
                rev = self._get_verifymd5(a.src_project, a.src_package, a.src_rev)
                self._check_package(a.src_project, a.src_package, rev, a.tgt_package, a.tgt_package)
            else:
                print >> sys.stderr, "unhandled request type %s"%a.type

    def _check_package(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s"%(src_project, src_package, src_rev, target_project, target_package))
        good = self._check_factory(src_rev, target_package)

        if not good:
            good = self._check_requests(src_rev, target_package)

        if good is None:
            self.logger.debug("ignoring")
        elif good:
            self.logger.debug("accepting")
        else:
            self.logger.debug("declining")
    
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

        self.logger.debug("srcmd5 not the latest version, checking history")
        u = osc.core.makeurl(self.apiurl, [ 'source', self.factory, package, '_history' ], { 'limit': '5' })
        try:
            r = osc.core.http_GET(u)
        except urllib2.HTTPError, e:
            self.logger.debug("package has no history!?")
            return None

        root = ET.parse(r).getroot()
        for revision in root.findall('revision'):
            node = revision.find('srcmd5')
            if node and node.text == rev:
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
                    else:
                        self.logger.debug("request still in review")
                        return None
        return False

    def _get_verifymd5(self, src_project, src_package, rev=None):
        query = { 'view': 'info' }
        if rev:
            query['rev'] = rev
        url = osc.core.makeurl(self.apiurl, ('source', src_project, src_package), query=query)
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return None

        if root is not None:
            srcmd5 = root.get('verifymd5')
            return srcmd5

class CommandLineInterface(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--factory", metavar="project", help="the openSUSE Factory project")
        parser.add_option("--apiurl", '-A', metavar="URL", help="api url")
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")

        return parser

    def postoptparse(self):
        logging.basicConfig()
        self.logger = logging.getLogger(self.optparser.prog)
        if (self.options.debug):
            self.logger.setLevel(logging.DEBUG)
        elif (self.options.verbose):
            self.logger.setLevel(logging.INFO)

        osc.conf.get_config(override_apiurl = self.options.apiurl)

        #if (self.options.debug):
        #    osc.conf.config['debug'] = 1

        self.checker = Checker(apiurl = osc.conf.config['apiurl'], \
                factory = self.options.factory, \
                dryrun = self.options.dry, \
                logger = self.logger)

    def do_id(self, subcmd, opts, *args):
        """${cmd_name}: print the status of working copy files and directories

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.checker.set_request_ids(args)
        self.checker.check_requests()

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
