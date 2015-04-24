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

from pprint import pprint
import os, sys, re
import logging
from optparse import OptionParser
import cmdln

import osc.conf
import osc.core
import urllib2

import ReviewBot

class TagChecker(ReviewBot.ReviewBot):
    """ simple bot that checks that a submit request has corrrect tags specified
    """

    def __init__(self, *args, **kwargs):
        super(TagChecker, self).__init__(*args, **kwargs)
        needed_tags=[r'bnc#[0-9]+',r'cve-[0-9]{4}-[0-9]+',r'fate#[0-9]+',r'boo#[0-9]+',r'bsc#[0-9]+', r'bgo#[0-9]+']
        self.needed_tags_re=[ re.compile(tag, re.IGNORECASE) for tag in needed_tags ]
	self.review_messages['declined'] = \
"""
(This is a script running, so report bugs)

We require a ID marked in .changes file to detect later if the changes 
are also merged into openSUSE:Factory. We accept bnc#, cve#, fate#, boo#, bsc# and bgo# atm.

Note: there is no whitespace behind before or after the number sign 
(compare with the packaging policies)
"""


    def textMatchesAnyTag(self, text):
        """
        Returns if the text parameter contains any of the needed tags
        """
        for tag_re in self.needed_tags_re:
            if tag_re.search(text):
                return True

        return False

    def changesFilesDiffsFromRequest(self, reqid):
        """
        Returns an array of strings, each one of which contains the diff of one .changes file modified by request reqid.
        This method should probably be moved to ReviewBot
        """

        diff = osc.core.request_diff(self.apiurl, reqid)
        changesdiffs=[]
        changesdiff=''
        inchanges = False
        for line in diff.split('\n'):
            if re.match(r'^---.*\.changes', line):
                if changesdiff:
                    changesdiffs.append(changesdiff)
                    changesdiff=''
                inchanges = True
            elif re.match(r'^---', line):
                if changesdiff:
                    changesdiffs.append(changesdiff)
                    changesdiff=''
                inchanges = False

            if inchanges:
                changesdiff+=line+'\n'

        if changesdiff:
            changesdiffs.append(changesdiff)
        return changesdiffs

    def checkTagInRequest(self, req, a):
        # First, we obtain the diff for each .changes file modified by the request
        diffs = self.changesFilesDiffsFromRequest(req.reqid)

        # If diffs is empty or just contains empty strings, there's no tag
        if not filter(None, diffs): 
            return False

        for diff in diffs:
            # each diff must contain at least one of the needed tags
            if not self.textMatchesAnyTag(diff):
               return False

        return True

    def check_action_submit(self, req, a):
        return self.checkTagInRequest(req,a)

    def check_action_maintenance_incident(self, req, a):
        return self.checkTagInRequest(req,a)

    def check_action_maintenance_release(self, req, a):
        return self.checkTagInRequest(req,a)

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")

        return TagChecker(apiurl = apiurl, \
                dryrun = self.options.dry, \
                group = self.options.group, \
                logger = self.logger)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
