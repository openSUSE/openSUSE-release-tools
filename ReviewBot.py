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
from collections import namedtuple
from osclib.memoize import memoize

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.conf
import osc.core
import urllib2

class ReviewBot(object):
    """
    A generic obs request reviewer
    Inherit from this class and implement check functions for each action type:

    def check_action_<type>(self, req, action):
        return (None|True|False)
    """

    DEFAULT_REVIEW_MESSAGES = { 'accepted' : 'ok', 'declined': 'review failed' }

    def __init__(self, apiurl = None, dryrun = False, logger = None, user = None, group = None):
        self.apiurl = apiurl
        self.dryrun = dryrun
        self.logger = logger
        self.review_user = user
        self.review_group = group
        self.requests = []
        self.review_messages = ReviewBot.DEFAULT_REVIEW_MESSAGES

    def set_request_ids(self, ids):
        for rqid in ids:
            u = osc.core.makeurl(self.apiurl, [ 'request', rqid ], { 'withhistory' : '1' })
            r = osc.core.http_GET(u)
            root = ET.parse(r).getroot()
            req = osc.core.Request()
            req.read(root)
            self.requests.append(req)

    def check_requests(self):
        for req in self.requests:
            self.logger.debug("checking %s"%req.reqid)
            good = self.check_one_request(req)

            if good is None:
                self.logger.info("ignoring")
            elif good:
                self.logger.info("%s is good"%req.reqid)
                self._set_review(req, 'accepted')
            else:
                self.logger.info("%s is not acceptable"%req.reqid)
                self._set_review(req, 'declined')

    def _set_review(self, req, state):
        doit = self.can_accept_review(req.reqid)
        if doit is None:
           self.logger.info("can't change state, %s does not have the reviewer"%(req.reqid))

        if doit == True:
            self.logger.debug("setting %s to %s"%(req.reqid, state))
            if not self.dryrun:
                msg = self.review_messages[state] if state in self.review_messages else state
                osc.core.change_review_state(apiurl = self.apiurl,
                        reqid = req.reqid, newstate = state, 
                        by_group=self.review_group,
                        by_user=self.review_user, message=msg)
        else:
            self.logger.debug("%s review not changed"%(req.reqid))

    def add_review(self, req, by_group=None, by_user=None, by_project = None, by_package = None, msg=None):
        query = {
            'cmd': 'addreview'
        }
        if by_group:
            query['by_group'] = by_group
        elif by_user:
            query['by_user'] = by_user
        elif by_project:
            query['by_project'] = by_project
            if by_package:
                query['by_package'] = by_package
        else:
            raise osc.oscerr.WrongArgs("missing by_*")

        u = osc.core.makeurl(self.apiurl, ['request', req.reqid], query)
        if self.dryrun:
            self.logger.info('POST %s' % u)
            return True

        try:
            r = osc.core.http_POST(u, data=msg)
        except urllib2.HTTPError, e:
            self.logger.error(e)
            return False

        code = ET.parse(r).getroot().attrib['code']
        if code != 'ok':
            self.logger.error("invalid return code %s"%code)
            return False

        return True

    def check_one_request(self, req):
        """
        check all actions in one request.

        calls helper functions for each action type

        return None if nothing to do, True to accept, False to reject
        """
        overall = None
        for a in req.actions:
            fn = 'check_action_%s'%a.type
            if not hasattr(self, fn):
                self.logger.error("unhandled request type %s"%a.type)
                ret = None
            else:
                func = getattr(self, fn)
                ret = func(req, a)
            if ret == False or overall is None and ret is not None:
                overall = ret
        return overall

    def check_action_maintenance_incident(self, req, a):
        return self.check_source_submission(a.src_project, a.src_package, a.src_rev, a.tgt_releaseproject, a.src_package)

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
        return self.check_source_submission(a.src_project, a.src_package, None, a.tgt_project, pkgname)

    def check_action_submit(self, req, a):
        return self.check_source_submission(a.src_project, a.src_package, a.src_rev, a.tgt_project, a.tgt_package)

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        """ default implemention does nothing """
        self.logger.info("%s/%s@%s -> %s/%s"%(src_project, src_package, src_rev, target_project, target_package))
        return None

    @staticmethod
    @memoize(session=True)
    def _get_sourceinfo(apiurl, project, package, rev=None):
        query = { 'view': 'info' }
        if rev is not None:
            query['rev'] = rev
        url = osc.core.makeurl(apiurl, ('source', project, package), query=query)
        try:
            return ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return None

    def get_originproject(self, project, package, rev=None):
        root = ReviewBot._get_sourceinfo(self.apiurl, project, package, rev)
        if root is None:
            return None

        originproject = root.find('originproject')
        if originproject is not None:
            return originproject.text

        return None

    def get_sourceinfo(self, project, package, rev=None):
        root = ReviewBot._get_sourceinfo(self.apiurl, project, package, rev)
        if root is None:
            return None

        props = ('package', 'rev', 'vrev', 'srcmd5', 'lsrcmd5', 'verifymd5')
        return namedtuple('SourceInfo', props)(*[ root.get(p) for p in props ])

    # TODO: what if there is more than _link?
    def _get_linktarget_self(self, src_project, src_package):
        """ if it's a link to a package in the same project return the name of the package"""
        prj, pkg = self._get_linktarget(src_project, src_package)
        if prj is None or prj == src_project:
            return pkg

    def _get_linktarget(self, src_project, src_package):

        query = {}
        url = osc.core.makeurl(self.apiurl, ('source', src_project, src_package), query=query)
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return (None, None)

        if root is not None:
            linkinfo = root.find("linkinfo")
            if linkinfo is not None:
                return (linkinfo.get('project'), linkinfo.get('package'))

        return (None, None)

    def can_accept_review(self, request_id):
        """return True if there is a new review for the specified reviewer"""
        states = set()
        url = osc.core.makeurl(self.apiurl, ('request', str(request_id)))
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
            if self.review_user:
                by_what = 'by_user'
                reviewer = self.review_user
            elif self.review_group:
                by_what = 'by_group'
                reviewer = self.review_group
            else:
                return False
            states = set([review.get('state') for review in root.findall('review') if review.get(by_what) == reviewer])
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        if not states:
            return None
        elif 'new' in states:
            return True
        return False

    def set_request_ids_search_review(self):
        if self.review_user:
           review = "@by_user='%s'+and+@state='new'"%self.review_user
        else:
           review = "@by_group='%s'+and+@state='new'"%self.review_group
        url = osc.core.makeurl(self.apiurl, ('search', 'request'), "match=state/@name='review'+and+review[%s]&withhistory=1"%review)
        root = ET.parse(osc.core.http_GET(url)).getroot()

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            self.requests.append(req)

class CommandLineInterface(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--apiurl", '-A', metavar="URL", help="api url")
        parser.add_option("--user",  metavar="USER", help="reviewer user name")
        parser.add_option("--group",  metavar="GROUP", help="reviewer group name")
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--osc-debug", action="store_true", help="osc debug output")
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

        if (self.options.osc_debug):
            osc.conf.config['debug'] = 1

        self.checker = self.setup_checker()

    def setup_checker(self):
        """ reimplement this """

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        group = self.options.group
        # if no args are given, use the current oscrc "owner"
        if user is None and group is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        return ReviewBot(apiurl = apiurl, \
                dryrun = self.options.dry, \
                user = user, \
                group = group, \
                logger = self.logger)

    def do_id(self, subcmd, opts, *args):
        """${cmd_name}: print the status of working copy files and directories

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.checker.set_request_ids(args)
        self.checker.check_requests()

    def do_review(self, subcmd, opts, *args):
        """${cmd_name}: print the status of working copy files and directories

        ${cmd_usage}
        ${cmd_option_list}
        """
        if self.checker.review_user is None and self.checker.review_group is None:
            raise osc.oscerr.WrongArgs("missing reviewer (user or group)")

        self.checker.set_request_ids_search_review()
        self.checker.check_requests()


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
