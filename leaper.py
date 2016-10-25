#!/usr/bin/python
# Copyright (c) 2014 SUSE Linux Products GmbH
# Copyright (c) 2016 SUSE LLC
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
import os
import sys
import re
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
import yaml
import ReviewBot
from check_maintenance_incidents import MaintenanceChecker
from check_source_in_factory import FactorySourceChecker

from osclib.comments import CommentAPI

class LogToString(logging.Filter):
    def __init__(self, obj, propname):
        self.obj = obj
        self.propname = propname

    def filter(self, record):
        if record.levelno >= logging.INFO:
            line = record.getMessage()
            comment_log = getattr(self.obj, self.propname)
            if comment_log is not None:
                comment_log.append(line)
            setattr(self.obj, self.propname, comment_log)
        return True

class Leaper(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        self.do_comments = True
        self.commentapi = CommentAPI(self.apiurl)

        self.maintbot = MaintenanceChecker(*args, **kwargs)
        # for FactorySourceChecker
        self.factory = FactorySourceChecker(*args, **kwargs)

        self.needs_reviewteam = False
        self.pending_factory_submission = False
        self.source_in_factory = None
        self.needs_release_manager = False
        self.release_manager_group = 'leap-reviewers'
        self.must_approve_version_updates = False
        self.must_approve_maintenance_updates = False

        self.comment_marker_re = re.compile(r'<!-- leaper state=(?P<state>done|seen)(?: result=(?P<result>accepted|declined))? -->')

        self.comment_log = None
        self.commentlogger = LogToString(self, 'comment_log')
        self.logger.addFilter(self.commentlogger)

    def prepare_review(self):

        # update lookup information on every run
        self.factory.parse_lookup('openSUSE:Leap:42.2')
        self.factory.parse_lookup('openSUSE:Leap:42.2:NonFree')
        self.lookup_422 = self.factory.lookup.copy()
        self.factory.lookup = {}
        self.factory.parse_lookup('openSUSE:Leap:42.1:Update')
        self.lookup_421 = self.factory.lookup.copy()
        self.factory.lookup = {}

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s"%(src_project, src_package, src_rev, target_project, target_package))
        src_srcinfo = self.get_sourceinfo(src_project, src_package, src_rev)
        package = target_package

        if src_srcinfo is None:
            # source package does not exist?
            # handle here to avoid crashing on the next line
            self.logger.warn("Could not get source info for %s/%s@%s" % (src_project, src_package, src_rev))
            return False

        origin = None
        if package in self.lookup_422:
            origin = self.lookup_422[package]

        is_fine_if_factory = False
        not_in_factory_okish = False
        if origin:
            self.logger.info("expected origin is '%s'", origin)
            if origin.startswith('Devel;'):
                (dummy, origin, dummy) = origin.split(';')
                if origin != src_project:
                    self.logger.debug("not submitted from devel project")
                    return False
                is_fine_if_factory = True
                not_in_factory_okish = True
                if self.must_approve_version_updates:
                    self.needs_release_manager = True
                # fall through to check history and requests
            elif origin.startswith('openSUSE:Factory'):
                if self.must_approve_version_updates:
                    self.needs_release_manager = True
                if origin == src_project:
                    self.source_in_factory = True
                    return True
                is_fine_if_factory = True
                # fall through to check history and requests
            elif origin == 'FORK':
                is_fine_if_factory = True
                not_in_factory_okish = True
                self.needs_release_manager = True
                # fall through to check history and requests
            elif origin.startswith('openSUSE:Leap:42.1'):
                if self.must_approve_maintenance_updates:
                    self.needs_release_manager = True
                # submitted from :Update
                if src_project.startswith(origin):
                    self.logger.debug("submission from 42.1 ok")
                    return True
                # submitted from elsewhere but is in :Update
                else:
                    good = self.factory._check_project('openSUSE:Leap:42.1:Update', target_package, src_srcinfo.verifymd5)
                    if good:
                        self.logger.info("submission found in 42.1")
                        return good
                    # check release requests too
                    good = self.factory._check_requests('openSUSE:Leap:42.1:Update', target_package, src_srcinfo.verifymd5)
                    if good or good == None:
                        self.logger.debug("found request")
                        return good
                # let's see where it came from before
                if package in self.lookup_421:
                    oldorigin = self.lookup_421[package]
                    self.logger.debug("oldorigin {}".format(oldorigin))
                    # Factory. So it's ok to keep upgrading it to Factory
                    # TODO: whitelist packages where this is ok and block others?
                    if oldorigin.startswith('openSUSE:Factory'):
                        self.logger.info("Package was from Factory in 42.1")
                        # check if an attempt to switch to SLE package is made
                        good = self.factory._check_project('SUSE:SLE-12-SP2:GA', target_package, src_srcinfo.verifymd5)
                        if good:
                            self.logger.info("request sources come from SLE")
                            self.needs_release_manager = True
                            return good
                # the release manager needs to review attempts to upgrade to Factory
                is_fine_if_factory = True
                self.needs_release_manager = True

            elif origin.startswith('SUSE:SLE-12'):
                if self.must_approve_maintenance_updates:
                    self.needs_release_manager = True
                # submitted from :Update
                if origin == src_project:
                    self.logger.debug("submission origin ok")
                    return True
                elif origin.endswith(':GA') \
                    and src_project == origin[:-2]+'Update':
                    self.logger.debug("sle update submission")
                    return True
                # submitted from higher SP
                if origin.startswith('SUSE:SLE-12:'):
                    if src_project.startswith('SUSE:SLE-12-SP1:') \
                        or src_project.startswith('SUSE:SLE-12-SP2:'):
                            self.logger.info("submission from service pack ok")
                            return True
                elif origin.startswith('SUSE:SLE-12-SP1:'):
                    if src_project.startswith('SUSE:SLE-12-SP2:'):
                        self.logger.info("submission from service pack ok")
                        return True

                self.needs_release_manager = True
                good = self._check_project_and_request('openSUSE:Leap:42.2:SLE-workarounds', target_package, src_srcinfo)
                if good or good == None:
                    self.logger.info("found sources in SLE-workarounds")
                    return good
                # the release manager needs to review attempts to upgrade to Factory
                is_fine_if_factory = True
            else:
                self.logger.error("unhandled origin %s", origin)
                return False
        else: # no origin
            # submission from SLE is ok
            if src_project.startswith('SUSE:SLE-12'):
                return True

            is_fine_if_factory = True
            self.needs_release_manager = True

        # we came here because none of the above checks find it good, so
        # let's see if the package is in Factory at least
        is_in_factory = self._check_factory(target_package, src_srcinfo)
        if is_in_factory:
            self.source_in_factory = True
            self.needs_reviewteam = False
        elif is_in_factory is None:
            self.pending_factory_submission = True
            self.needs_reviewteam = False
        else:
            if src_project.startswith('SUSE:SLE-12') \
                or src_project.startswith('openSUSE:Leap:42.'):
                self.needs_reviewteam = False
            else:
                self.needs_reviewteam = True
            self.source_in_factory = False

        if is_fine_if_factory:
            if self.source_in_factory:
                return True
            elif self.pending_factory_submission:
                return None
            elif not_in_factory_okish:
                self.needs_reviewteam = True
                return True

        return False

    def _check_factory(self, target_package, src_srcinfo):
            good = self.factory._check_project('openSUSE:Factory', target_package, src_srcinfo.verifymd5)
            if good:
                return good
            good = self.factory._check_requests('openSUSE:Factory', target_package, src_srcinfo.verifymd5)
            if good or good == None:
                self.logger.debug("found request to Factory")
                return good
            good = self.factory._check_project('openSUSE:Factory:NonFree', target_package, src_srcinfo.verifymd5)
            if good:
                return good
            good = self.factory._check_requests('openSUSE:Factory:NonFree', target_package, src_srcinfo.verifymd5)
            if good or good == None:
                self.logger.debug("found request to Factory:NonFree")
                return good
            return False

    def _check_project_and_request(self, project, target_package, src_srcinfo):
        good = self.factory._check_project(project, target_package, src_srcinfo.verifymd5)
        if good:
            return good
        good = self.factory._check_requests(project, target_package, src_srcinfo.verifymd5)
        if good or good == None:
            return good
        return False

    def check_one_request(self, req):
        self.review_messages = self.DEFAULT_REVIEW_MESSAGES.copy()
        self.needs_reviewteam = False
        self.needs_release_manager = False
        self.pending_factory_submission = False
        self.source_in_factory = None
        self.comment_log = []

        if len(req.actions) != 1:
            msg = "only one action per request please"
            self.review_messages['declined'] = msg
            return False

        request_ok = ReviewBot.ReviewBot.check_one_request(self, req)
        has_correct_maintainer = self.maintbot.check_one_request(req)

        self.logger.debug("review result: %s", request_ok)
        self.logger.debug("has_correct_maintainer: %s", has_correct_maintainer)
        if self.pending_factory_submission:
            self.logger.info("submission is waiting for a Factory request to complete")
        elif self.source_in_factory:
            self.logger.info("the submitted sources are in or accepted for Factory")
        elif self.source_in_factory == False:
            self.logger.info("the submitted sources are NOT in Factory")

        if request_ok == False:
            self.logger.info("NOTE: if you think the automated review was wrong here, please talk to the release team before reopening the request")
        elif self.needs_release_manager:
            self.logger.info("request needs review by release management")

        if self.comment_log:
            result = None
            if request_ok is None:
                state = 'seen'
            elif request_ok:
                state = 'done'
                result = 'accepted'
            else:
                state = 'done'
                result = 'declined'
            self.add_comment(req, '\n\n'.join(self.comment_log), state)
        self.comment_log = None

        if self.needs_release_manager:
            add_review = True
            for r in req.reviews:
                if r.by_group == self.release_manager_group and (r.state == 'new' or r.state == 'accepted'):
                    add_review = False
                    self.logger.debug("%s already is a reviewer", self.release_manager_group)
                    break
            if add_review:
                if self.add_review(req, by_group = self.release_manager_group) != True:
                    self.review_messages['declined'] += '\nadding %s failed' % self.release_manager_group
                    return False

        if self.needs_reviewteam:
            add_review = True
            self.logger.info("%s needs review by opensuse-review-team"%req.reqid)
            for r in req.reviews:
                if r.by_group == 'opensuse-review-team':
                    add_review = False
                    self.logger.debug("opensuse-review-team already is a reviewer")
                    break
            if add_review:
                if self.add_review(req, by_group = "opensuse-review-team") != True:
                    self.review_messages['declined'] += '\nadding opensuse-review-team failed'
                    return False

        return request_ok

    def check_action__default(self, req, a):
        # decline all other requests for fallback reviewer
        self.logger.debug("auto decline request type %s"%a.type)
        return False

    # TODO: make generic, move to Reviewbot. Used by multiple bots
    def add_comment(self, req, msg, state, result=None):
        if not self.do_comments:
            return

        comment = "<!-- leaper state=%s%s -->\n" % (state, ' result=%s' % result if result else '')
        comment += "\n" + msg

        (comment_id, comment_state, comment_result, comment_text) = self.find_obs_request_comment(req, state)

        if comment_id is not None and state == comment_state:
            # count number of lines as aproximation to avoid spamming requests
            # for slight wording changes in the code
            if len(comment_text.split('\n')) == len(comment.split('\n')):
                self.logger.debug("not worth the update, previous comment %s is state %s", comment_id, comment_state)
                return

        self.logger.debug("adding comment to %s, state %s result %s", req.reqid, state, result)
        self.logger.debug("message: %s", msg)
        if not self.dryrun:
            if comment_id is not None:
                self.commentapi.delete(comment_id)
            self.commentapi.add_comment(request_id=req.reqid, comment=str(comment))

    def find_obs_request_comment(self, req, state=None):
        """Return previous comments (should be one)."""
        if self.do_comments:
            comments = self.commentapi.get_comments(request_id=req.reqid)
            for c in comments.values():
                m = self.comment_marker_re.match(c['comment'])
                if m and (state is None or state == m.group('state')):
                    return c['id'], m.group('state'), m.group('result'), c['comment']
        return None, None, None, None

    def check_action__default(self, req, a):
        self.logger.info("unhandled request type %s"%a.type)
        self.needs_release_manager = True
        return True

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option("--no-comment", dest='comment', action="store_false", default=True, help="don't actually post comments to obs")
        parser.add_option("--manual-version-updates", action="store_true", help="release manager must approve version updates")
        parser.add_option("--manual-maintenance-updates", action="store_true", help="release manager must approve maintenance updates")

        return parser

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        group = self.options.group
        # if no args are given, use the current oscrc "owner"
        if user is None and group is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        bot = Leaper(apiurl = apiurl, \
                dryrun = self.options.dry, \
                user = user, \
                group = group, \
                logger = self.logger)

        if self.options.manual_version_updates:
            bot.must_approve_version_updates = True
        if self.options.manual_maintenance_updates:
            bot.must_approve_maintenance_updates = True
        bot.do_comments = self.options.comment

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
