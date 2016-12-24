#!/usr/bin/python
# Copyright (c) 2014-2016 SUSE LLC
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
import signal
import datetime

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
    REVIEW_CHOICES = ('normal', 'no', 'accept', 'accept-onpass', 'fallback-onfail', 'fallback-always')

    def __init__(self, apiurl = None, dryrun = False, logger = None, user = None, group = None):
        self.apiurl = apiurl
        self.dryrun = dryrun
        self.logger = logger
        self.review_user = user
        self.review_group = group
        self.requests = []
        self.review_messages = ReviewBot.DEFAULT_REVIEW_MESSAGES
        self._review_mode = 'normal'
        self.fallback_user = None
        self.fallback_group = None

    @property
    def review_mode(self):
        return self._review_mode

    @review_mode.setter
    def review_mode(self, value):
        if value not in self.REVIEW_CHOICES:
            raise Exception("invalid review option: %s"%value)
        self._review_mode = value

    def set_request_ids(self, ids):
        for rqid in ids:
            u = osc.core.makeurl(self.apiurl, [ 'request', rqid ], { 'withhistory' : '1' })
            r = osc.core.http_GET(u)
            root = ET.parse(r).getroot()
            req = osc.core.Request()
            req.read(root)
            self.requests.append(req)

    # function called before requests are reviewed
    def prepare_review(self):
        pass

    def check_requests(self):

        # give implementations a chance to do something before single requests
        self.prepare_review()
        for req in self.requests:
            self.logger.info("checking %s"%req.reqid)
            good = self.check_one_request(req)

            if self.review_mode == 'no':
                good = None
            elif self.review_mode == 'accept':
                good = True

            if good is None:
                self.logger.info("%s ignored"%req.reqid)
            elif good:
                self._set_review(req, 'accepted')
            elif self.review_mode != 'accept-onpass':
                self._set_review(req, 'declined')

    def _set_review(self, req, state):
        doit = self.can_accept_review(req.reqid)
        if doit is None:
           self.logger.info("can't change state, %s does not have the reviewer"%(req.reqid))

        newstate = state

        by_user = self.fallback_user
        by_group = self.fallback_group

        if state == 'declined':
            if self.review_mode == 'fallback-onfail':
                self.logger.info("%s needs fallback reviewer"%req.reqid)
                # don't check duplicates, in case review was re-opened
                self.add_review(req, by_group=by_group, by_user=by_user)
                newstate = 'accepted'
        elif self.review_mode == 'fallback-always':
            self.add_review(req, by_group=by_group, by_user=by_user)

        msg = self.review_messages[state] if state in self.review_messages else state
        self.logger.info("%s %s: %s"%(req.reqid, state, msg))

        if doit == True:
            self.logger.debug("setting %s to %s"%(req.reqid, state))
            if not self.dryrun:
                osc.core.change_review_state(apiurl = self.apiurl,
                        reqid = req.reqid, newstate = newstate,
                        by_group=self.review_group,
                        by_user=self.review_user, message=msg)
        else:
            self.logger.debug("%s review not changed"%(req.reqid))

    # note we intentionally don't check for duplicate review here!
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
                fn = 'check_action__default'
            func = getattr(self, fn)
            ret = func(req, a)
            if ret == False or overall is None and ret is not None:
                overall = ret
        return overall

    def check_action_maintenance_incident(self, req, a):
        dst_package = a.src_package
        # Ignoring patchinfo package for checking
        if a.src_package == 'patchinfo':
          self.logger.info("package is patchinfo, ignoring")
          return None
        # dirty obs crap
        if a.tgt_releaseproject is not None:
            ugly_suffix = '.'+a.tgt_releaseproject.replace(':', '_')
            if dst_package.endswith(ugly_suffix):
                dst_package = dst_package[:-len(ugly_suffix)]
        return self.check_source_submission(a.src_project, a.src_package, a.src_rev, a.tgt_releaseproject, dst_package)

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

    def check_action__default(self, req, a):
        self.logger.error("unhandled request type %s"%a.type)
        ret = None

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
        except (urllib2.HTTPError, urllib2.URLError):
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

        self.requests = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            self.requests.append(req)

    def set_request_ids_project(self, project, typename):
        url = osc.core.makeurl(self.apiurl, ('search', 'request'),
            "match=(state/@name='review'+or+state/@name='new')+and+(action/target/@project='%s'+and+action/@type='%s')&withhistory=1"%(project, typename))
        root = ET.parse(osc.core.http_GET(url)).getroot()

        self.requests = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            self.requests.append(req)

    def set_request_ids_project(self, project, typename):
        url = osc.core.makeurl(self.apiurl, ('search', 'request'),
            "match=(state/@name='review'+or+state/@name='new')+and+(action/target/@project='%s'+and+action/@type='%s')&withhistory=1"%(project, typename))
        root = ET.parse(osc.core.http_GET(url)).getroot()

        self.requests = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            self.requests.append(req)

class CommandLineInterface(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = cmdln.Cmdln.get_optparser(self)
        parser.add_option("--apiurl", '-A', metavar="URL", help="api url")
        parser.add_option("--user",  metavar="USER", help="reviewer user name")
        parser.add_option("--group",  metavar="GROUP", help="reviewer group name")
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--osc-debug", action="store_true", help="osc debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        parser.add_option("--review-mode", dest='review_mode', choices=ReviewBot.REVIEW_CHOICES, help="review behavior")
        parser.add_option("--fallback-user", dest='fallback_user', metavar='USER', help="fallback review user")
        parser.add_option("--fallback-group", dest='fallback_group', metavar='GROUP', help="fallback review group")

        return parser

    def postoptparse(self):
        level = None
        if (self.options.debug):
            level = logging.DEBUG
        elif (self.options.verbose):
            level = logging.INFO

        logging.basicConfig(level=level)
        self.logger = logging.getLogger(self.optparser.prog)

        osc.conf.get_config(override_apiurl = self.options.apiurl)

        if (self.options.osc_debug):
            osc.conf.config['debug'] = 1

        self.checker = self.setup_checker()

        if self.options.review_mode:
            self.checker.review_mode = self.options.review_mode

        if self.options.fallback_user:
            self.checker.fallback_user = self.options.fallback_user

        if self.options.fallback_group:
            self.checker.fallback_group = self.options.fallback_group

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
        """${cmd_name}: check the specified request ids

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.checker.set_request_ids(args)
        self.checker.check_requests()

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    def do_review(self, subcmd, opts, *args):
        """${cmd_name}: check requests that have the specified user or group as reviewer

        ${cmd_usage}
        ${cmd_option_list}
        """
        if self.checker.review_user is None and self.checker.review_group is None:
            raise osc.oscerr.WrongArgs("missing reviewer (user or group)")

        def work():
            self.checker.set_request_ids_search_review()
            self.checker.check_requests()

        self.runner(work, opts.interval)

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    def do_project(self, subcmd, opts, project, typename):
        """${cmd_name}: check all requests of specified type to specified

        ${cmd_usage}
        ${cmd_option_list}
        """

        def work():
            self.checker.set_request_ids_project(project, typename)
            self.checker.check_requests()

        self.runner(work, opts.interval)

    def runner(self, workfunc, interval):
        """ runs the specified callback every <interval> minutes or
        once if interval is None or 0
        """
        class ExTimeout(Exception):
            """raised on timeout"""

        if interval:
            def alarm_called(nr, frame):
                raise ExTimeout()
            signal.signal(signal.SIGALRM, alarm_called)

        while True:
            try:
                workfunc()
            except Exception, e:
                self.logger.exception(e)

            if interval:
                self.logger.info("sleeping %d minutes. Press enter to check now ..."%interval)
                signal.alarm(interval*60)
                try:
                    raw_input()
                except ExTimeout:
                    pass
                signal.alarm(0)
                self.logger.info("recheck at %s"%datetime.datetime.now().isoformat())
                continue
            break

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
