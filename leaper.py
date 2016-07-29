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
import yaml
import ReviewBot
from check_maintenance_incidents import MaintenanceChecker
from check_source_in_factory import FactorySourceChecker

class Leaper(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.maintbot = MaintenanceChecker(*args, **kwargs)
        # for FactorySourceChecker
        self.factory = FactorySourceChecker(*args, **kwargs)

        self.needs_reviewteam = False
        self.pending_factory_submission = False
        self.source_in_factory = False

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

        if origin:
            self.logger.debug("origin {}".format(origin))
            if origin.startswith('Devel;'):
                self.needs_reviewteam = True
                (dummy, origin, dummy) = origin.split(';')
            if origin == src_project:
                self.logger.debug("exact match")
                return True
            elif origin.startswith('openSUSE:Factory'):
                return self._check_factory(target_package, src_srcinfo)
            elif origin.startswith('openSUSE:Leap:42.1'):
                # submitted from :Update
                if src_project.startswith(origin):
                    self.logger.debug("match 42.1")
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
                        if src_project == oldorigin:
                            self.logger.debug("Upgrade to Factory again. Submitted from Factory")
                            return True
                        good = self._check_factory(target_package, src_srcinfo)
                        if good or good == None:
                            self.logger.debug("Upgrade to Factory again. It's in Factory")
                            return good
                        # or maybe in SP2?
                        good = self.factory._check_project('SUSE:SLE-12-SP2:GA', target_package, src_srcinfo.verifymd5)
                        if good:
                            self.logger.debug("hope it's ok to change to SP2")
                            return good
                # else other project or FORK, fall through

            elif origin.startswith('SUSE:SLE-12'):
                # submitted from :Update
                if src_project.startswith(origin):
                    self.logger.debug("match sle")
                    return True
                # submitted from higher SP
                if origin.startswith('SUSE:SLE-12'):
                    if src_project.startswith('SUSE:SLE-12-SP1') \
                        or src_project.startswith('SUSE:SLE-12-SP2'):
                            self.logger.debug("higher service pack ok")
                            return True
            # else other project or FORK, fall through

            # we came here because none of the above checks find it good, so
            # let's see if the package is in Factory at least
            is_in_factory = self._check_factory(target_package, src_srcinfo)
            if is_in_factory:
                self.source_in_factory = True
            elif is_in_factory is None:
                self.pending_factory_submission = True
            else:
                self.needs_reviewteam = True

        else: # no origin
            # SLE and Factory are ok
            if src_project.startswith('SUSE:SLE-12') \
                or src_project.startswith('openSUSE:Factory'):
                return True
            # submitted from elsewhere, check it's in Factory
            good = self._check_factory(target_package, src_srcinfo)
            if good:
                self.source_in_factory = True
                return True
            elif good == None:
                self.pending_factory_submission = True
                return good
            # or maybe in SP2?
            good = self.factory._check_project('SUSE:SLE-12-SP2:GA', target_package, src_srcinfo.verifymd5)
            if good:
                return good

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

    def check_one_request(self, req):
        self.review_messages = self.DEFAULT_REVIEW_MESSAGES.copy()
        self.needs_reviewteam = False
        self.pending_factory_submission = False
        self.source_in_factory = False

        if len(req.actions) != 1:
            msg = "only one action per request please"
            self.review_messages['declined'] = msg
            return False

        # if the fallback reviewer created the request she probably
        # knows what she does :-)
        if self.fallback_user and req.get_creator() == self.fallback_user:
            self.logger.debug("skip fallback review")
            return True

        has_upstream_sources = ReviewBot.ReviewBot.check_one_request(self, req)
        has_correct_maintainer = self.maintbot.check_one_request(req)

        # not reviewed yet?
        if has_upstream_sources is None:
            return None

        self.logger.debug("upstream sources: {}, maintainer ok: {}".format(has_upstream_sources, has_correct_maintainer))

        if self.needs_reviewteam:
            add_review = True
            self.logger.debug("%s needs review by opensuse-review-team"%req.reqid)
            for r in req.reviews:
                if r.by_group == 'opensuse-review-team':
                    add_review = False
                    self.logger.debug("opensuse-review-team already is a reviewer")
                    break
            if add_review:
                if self.add_review(req, by_group = "opensuse-review-team") != True:
                    self.review_messages['declined'] += '\nadding opensuse-review-team failed'
                    return False

        if has_upstream_sources != True or has_correct_maintainer != True:
            if has_upstream_sources != True:
                self.review_messages['declined'] += '\nOrigin project changed'
                pkg = req.actions[0].tgt_package
                if pkg in self.lookup_422:
                    self.review_messages['declined'] += '(was {})'.format(self.lookup_422[pkg])
                if self.source_in_factory:
                    self.review_messages['declined'] += '\nsource is in Factory'
                if self.pending_factory_submission:
                    self.review_messages['declined'] += '\na submission to Factory is pending'
                    self.logger.debug("origin changed but waiting for Factory submission to complete")
                    # FXIME: we should add the human reviewer here
                    # and leave a comment
                    return None
            # shouldn't happen actually
            if has_correct_maintainer != True:
                self.review_messages['declined'] += '\nMaintainer check failed'
            return False

        return True

    def check_action__default(self, req, a):
        # decline all other requests for fallback reviewer
        self.logger.debug("auto decline request type %s"%a.type)
        return False

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

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

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
