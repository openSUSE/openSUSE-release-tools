#!/usr/bin/python
# Copyright (c) 2014 SUSE Linux GmbH
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

class MaintenanceChecker(ReviewBot.ReviewBot):
    """ simple bot that adds other reviewers depending on target project
    """

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.review_messages = {}

    # XXX: share with checkrepo
    def _maintainers(self, package):
        """Get the maintainer of the package involved in the package."""
        query = {
            'binary': package,
        }
        url = osc.core.makeurl(self.apiurl, ('search', 'owner'), query=query)
        root = ET.parse(osc.core.http_GET(url)).getroot()
        return [p.get('name') for p in root.findall('.//person') if p.get('role') == 'maintainer']

    def add_devel_project_review(self, req, package):
        """ add devel project/package as reviewer """
        query = {
            'binary': package,
        }
        url = osc.core.makeurl(self.apiurl, ('search', 'owner'), query=query)
        root = ET.parse(osc.core.http_GET(url)).getroot()

        package_reviews = set((r.by_project, r.by_package) for r in req.reviews if r.by_package)
        for p in root.findall('./owner'):
            prj = p.get("project")
            pkg = p.get("package")
            if ((prj, pkg) in package_reviews):
                # there already is a review for this project/package
                continue
            self.add_review(req, by_project = prj, by_package = pkg,
                    msg = "Submission by someone who is not maintainer in the devel project. Please review")

    def check_action_maintenance_incident(self, req, a):
        known_maintainer = False
        author = req.get_creator()
        # check if there is a link and use that or the real package
        # name as src_packge may end with something like
        # .openSUSE_XX.Y_Update
        pkgname = a.src_package
        (linkprj, linkpkg) = self._get_linktarget(a.src_project, pkgname)
        if linkpkg is not None:
            pkgname = linkpkg
        if pkgname == 'patchinfo':
            return None

        maintainers = set(self._maintainers(pkgname))
        if maintainers:
            for m in maintainers:
                if author == m:
                    self.logger.debug("%s is maintainer"%author)
                    known_maintainer = True
            if not known_maintainer:
                for r in req.reviews:
                    if r.by_user in maintainers:
                        self.logger.debug("found %s as reviewer"%r.by_user)
                        known_maintainer = True
            if not known_maintainer:
                self.logger.info("author: %s, maintainers: %s => need review"%(author, ','.join(maintainers)))
                self.needs_maintainer_review.add(pkgname)
        else:
            self.logger.warning("%s doesn't have maintainers"%pkgname)
            self.needs_maintainer_review.add(pkgname)

        if a.tgt_releaseproject == "openSUSE:Backports:SLE-12":
            self.add_factory_source = True

        return True

    def check_one_request(self, req):
        self.add_factory_source = False
        self.needs_maintainer_review = set()

        ret = ReviewBot.ReviewBot.check_one_request(self, req)

        if self.add_factory_source:
            self.logger.debug("%s needs review by factory-source"%req.reqid)
            if self.add_review(req, by_user =  "factory-source") != True:
                ret = None

        if self.needs_maintainer_review:
            for p in self.needs_maintainer_review:
                self.add_devel_project_review(req, p)

        return ret

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        if user is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        return MaintenanceChecker(apiurl = apiurl, \
                dryrun = self.options.dry, \
                user = user, \
                logger = self.logger)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

# vim: sw=4 et
