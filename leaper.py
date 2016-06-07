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
        self.factory.parse_lookup('openSUSE:Leap:42.2')

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        return self.factory.check_source_submission(src_project, src_package, src_rev, target_project, target_package)

    def check_one_request(self, req):
        self.review_messages = self.DEFAULT_REVIEW_MESSAGES.copy()

        if len(req.actions) != 1:
            msg = "only one action per request please"
            self.review_messages['accepted'] = msg
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

        if has_upstream_sources != True or has_correct_maintainer != True:
            if has_upstream_sources != True:
                self.review_messages['accepted'] += '\nOrigin project changed'
            # shouldn't happen actually
            if has_correct_maintainer != True:
                self.review_messages['accepted'] += '\nMaintainer check failed'
            return False

        return True

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
