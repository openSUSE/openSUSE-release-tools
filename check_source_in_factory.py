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
import yaml
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
        self.lookup = None

    def parse_lookup(self, project):
        self.lookup = yaml.safe_load(self._load_lookup_file(project))

    def _load_lookup_file(self, prj):
        if prj.endswith(':NonFree'):
            prj = prj[:-len(':NonFree')]
        return osc.core.http_GET(osc.core.makeurl(self.apiurl,
                                ['source', prj, '00Meta', 'lookup.yml']))

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s"%(src_project, src_package, src_rev, target_project, target_package))
        src_srcinfo = self.get_sourceinfo(src_project, src_package, src_rev)
        if src_srcinfo is None:
            # source package does not exist?
            # handle here to avoid crashing on the next line
            self.logger.info("Could not get source info for %s/%s@%s" % (src_project, src_package, src_rev))
            return False
        good = self._check_factory(src_srcinfo.verifymd5, target_package)

        if good:
            self.logger.info("%s is in Factory"%target_package)
            return good

        good = self._check_requests(src_srcinfo.verifymd5, target_package)
        if good:
            self.logger.info("%s already reviewed for Factory"%target_package)

        return good

    def _package_get_upstream_project(self, package):
        """ return project where the specified pacakge is supposed to come
        from. Either by lookup table or self.factory """
        if self.lookup and package in self.lookup:
            return self.lookup[package]

        return self.factory

    def _check_factory(self, rev, package):
        """check if factory sources contain the package and revision. check head and history"""
        project = self._package_get_upstream_project(package)
        if project is None:
            return False
        self.logger.debug("checking %s in %s"%(package, project))
        try:
            si = osc.core.show_package_meta(self.apiurl, project, package)
        except (urllib2.HTTPError, urllib2.URLError):
            si = None
        if si is None:
            self.logger.debug("new package")
            return None
        else:
            si = self.get_sourceinfo(project, package)
            if rev == si.verifymd5:
                self.logger.debug("srcmd5 matches")
                return True

        self.logger.debug("%s not the latest version, checking history", rev)
        u = osc.core.makeurl(self.apiurl, [ 'source', project, package, '_history' ], { 'limit': '5' })
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
        try:
            project = self._package_get_upstream_project(package)
            if project is None:
                self.logger.error("no upstream project found for {}, can't check requests".format(package))
                return None
            requests = osc.core.get_request_list(self.apiurl, project, package, None, ['new', 'review'], 'submit')
        except (urllib2.HTTPError, urllib2.URLError):
            self.logger.debug("none request")
            return None

        for req in requests:
            for a in req.actions:
                si = self.get_sourceinfo(a.src_project, a.src_package, a.src_rev)
                self.logger.debug("rq %s: %s/%s@%s"%(req.reqid, a.src_project, a.src_package, si.verifymd5))
                if si.verifymd5 == rev:
                    if req.state.name == 'new':
                        self.logger.debug("request ok")
                        return True
                    elif req.state.name == 'review':
                        self.logger.info("request still in review")
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
        parser.add_option("--lookup", metavar="project", help="use lookup file")

        return parser

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        if user is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        bot = FactorySourceChecker(apiurl = apiurl, \
                factory = self.options.factory, \
                dryrun = self.options.dry, \
                user = user, \
                logger = self.logger)

        if self.options.lookup:
            bot.parse_lookup(self.options.lookup)

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
