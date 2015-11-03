#!/usr/bin/python
# -*- coding: utf-8 -*-
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

import sys
import re

import osc.conf
import osc.core

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

try:
    from urllib.error import URLError
except ImportError:
    # python 2.x
    from urllib2 import URLError

import ReviewBot

import check_source_in_factory


class TagChecker(ReviewBot.ReviewBot):
    """ simple bot that checks that a submit request has corrrect tags specified
    """

    def __init__(self, *args, **kwargs):
        self.factory = None
        if 'factory' in kwargs:
            self.factory = kwargs['factory']
            del kwargs['factory']
        if self.factory is None:
            self.factory = "openSUSE:Factory"
        super(TagChecker, self).__init__(*args, **kwargs)
        needed_tags = [r'bnc#[0-9]+',
                       r'cve-[0-9]{4}-[0-9]+',
                       r'fate#[0-9]+',
                       r'boo#[0-9]+',
                       r'bsc#[0-9]+',
                       r'bgo#[0-9]+']
        self.needed_tags_re = [re.compile(tag, re.IGNORECASE) for tag in needed_tags]
        self.review_messages['declined'] = """
(This is a script running, so report bugs)

We require a ID marked in .changes file to detect later if the changes
are also merged into openSUSE:Factory. We accept bnc#, cve#, fate#, boo#, bsc# and bgo# atm.

Note: there is no whitespace behind before or after the number sign
(compare with the packaging policies)
"""

    def isNewPackage(self, tgt_project, tgt_package):
        try:
            self.logger.debug("package_meta %s %s/%s" % (self.apiurl, tgt_project, tgt_package))
            osc.core.show_package_meta(self.apiurl, tgt_project, tgt_package)
        except (HTTPError, URLError):
            return True
        return False

    def checkTagInRequest(self, req, a):
        is_new = False
        u = osc.core.makeurl(self.apiurl,
                             ['source', a.tgt_project, a.tgt_package],
                             {'cmd': 'diff',
                              'onlyissues': '1',
                              'view': 'xml',
                              'opackage': a.src_package,
                              'oproject': a.src_project,
                              'orev': a.src_rev})
        try:
            f = osc.core.http_POST(u)
        except (HTTPError, URLError):
            is_new = self.isNewPackage(a.tgt_project, a.tgt_package)
        
        # in case the quest have not the matched revision in Factory
        # and it is a new package to target project, then leave it to
        # human review
        if is_new:
            self.logger.info("New package to %s and have not the matched revision in Factory"%a.tgt_project)
            return True

        xml = ET.parse(f)
        has_changes = list(xml.findall('./issues/issue'))
        if not has_changes:
            self.logger.debug("reject: diff contains no tags")
            return False
        return True

    def checkTagNotRequired(self, req, a):
        # if there is no diff, no tag is required
        diff = osc.core.request_diff(self.apiurl, req.reqid)
        if not diff:
            return True

        # 1) A tag is not required only if the package is
        # already in Factory with the same revision,
        # and the package is being introduced, not updated
        # 2) A new package must be have a issue tag
        factory_checker = check_source_in_factory.FactorySourceChecker(apiurl=self.apiurl,
                                                                       dryrun=self.dryrun,
                                                                       logger=self.logger,
                                                                       user=self.review_user,
                                                                       group=self.review_group,
                                                                       factory=self.factory)
        factory_ok = factory_checker.check_source_submission(a.src_project, a.src_package, a.src_rev,
                                                             a.tgt_project, a.tgt_package)
        return factory_ok

    def checkTagNotRequiredOrInRequest(self, req, a):
        if self.checkTagNotRequired(req, a):
            return True
        return self.checkTagInRequest(req, a)

    def check_action_submit(self, req, a):
        return self.checkTagNotRequiredOrInRequest(req, a)

    def check_action_delete(self, req, a):
        self.logger.info("Delete package %s/%s"%(a.tgt_package, req.reqid))
        # accept delete request automatically
        return True

    def check_action_add_role(self, req, a):
        self.logger.info("add_role request %s, accepting..."%req.reqid)
        # accept add_role request automatically
        return True

    def check_action_maintenance_incident(self, req, a):
        return self.checkTagInRequest(req, a)

    def check_action_maintenance_release(self, req, a):
        return self.checkTagInRequest(req, a)


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

        return TagChecker(apiurl=apiurl,
                          factory=self.options.factory,
                          dryrun=self.options.dry,
                          group=self.options.group,
                          logger=self.logger)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
