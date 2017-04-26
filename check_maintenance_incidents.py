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
import yaml

from osclib.memoize import memoize

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
        root = osc.core.owner(self.apiurl, package)
        maintainers = [p.get('name') for p in root.findall('.//person') if p.get('role') == 'maintainer']
        if not maintainers:
            for group in [p.get('name') for p in root.findall('.//group') if p.get('role') == 'maintainer']:
                url = osc.core.makeurl(self.apiurl, ('group', group))
                root = ET.parse(osc.core.http_GET(url)).getroot()
                maintainers = maintainers + [p.get('userid') for p in root.findall('./person/person')]
        return maintainers

    def add_devel_project_review(self, req, package):
        """ add devel project/package as reviewer """
        root = osc.core.owner(self.apiurl, package)

        package_reviews = set((r.by_project, r.by_package) for r in req.reviews if r.by_project)
        for p in root.findall('./owner'):
            prj = p.get("project")
            pkg = p.get("package")
            # packages dropped from Factory sometimes point to maintained distros
            if prj.startswith('openSUSE:Leap') or prj.startswith('openSUSE:1'):
                self.logger.debug("%s looks wrong as maintainer, skipped", prj)
                continue
            if ((prj, pkg) in package_reviews):
                self.logger.debug("%s/%s already is a reviewer, not adding again" % (prj, pkg))
                continue
            self.add_review(req, by_project = prj, by_package = pkg,
                    msg = 'Submission for {} by someone who is not maintainer in the devel project ({}). Please review'.format(pkg, prj) )

    @staticmethod
    @memoize(session=True)
    def _get_lookup_yml(apiurl, project):
        """ return a dictionary with package -> project mapping
        """
        url = osc.core.makeurl(apiurl, ('source', project, '00Meta', 'lookup.yml'))
        try:
            return yaml.safe_load(osc.core.http_GET(url))
        except (urllib2.HTTPError, urllib2.URLError):
            return None

    # check if pkgname was submitted by the correct maintainer. If not, set
    # self.needs_maintainer_review
    def _check_maintainer_review_needed(self, req, a):
        author = req.get_creator()
        if a.type == 'maintenance_incident':
            # check if there is a link and use that or the real package
            # name as src_packge may end with something like
            # .openSUSE_XX.Y_Update
            pkgname = a.src_package
            (linkprj, linkpkg) = self._get_linktarget(a.src_project, pkgname)
            if linkpkg is not None:
                pkgname = linkpkg
            if pkgname == 'patchinfo':
                return None

            project = a.tgt_releaseproject
        else:
            pkgname = a.tgt_package
            project = a.tgt_project

        if project.startswith('openSUSE:Leap:'):
            mapping = MaintenanceChecker._get_lookup_yml(self.apiurl, project)
            if mapping is None:
                self.logger.error("error loading mapping for {}".format(project))
            elif not pkgname in mapping:
                self.logger.debug("{} not tracked".format(pkgname))
            else:
                origin = mapping[pkgname]
                self.logger.debug("{} comes from {}, submitted from {}".format(pkgname, origin, a.src_project))
                if origin.startswith('SUSE:SLE-12') and a.src_project.startswith('SUSE:SLE-12') \
                    or origin.startswith('openSUSE:Leap') and a.src_project.startswith('openSUSE:Leap'):
                    self.logger.info("{} submitted from {}, no maintainer review needed".format(pkgname, a.src_project))
                    return

        maintainers = set(self._maintainers(pkgname))
        if maintainers:
            known_maintainer = False
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
                self.logger.debug("author: %s, maintainers: %s => need review"%(author, ','.join(maintainers)))
                self.needs_maintainer_review.add(pkgname)
        else:
            self.logger.warning("%s doesn't have maintainers"%pkgname)
            self.needs_maintainer_review.add(pkgname)

    def check_action_maintenance_incident(self, req, a):

        if a.src_package == 'patchinfo':
            return None

        self._check_maintainer_review_needed(req, a)

        if a.tgt_releaseproject.startswith("openSUSE:Backports:"):
            self.add_factory_source = True

        return True

    def check_action_submit(self, req, a):

        self._check_maintainer_review_needed(req, a)

        return True


    def check_one_request(self, req):
        self.add_factory_source = False
        self.needs_maintainer_review = set()

        ret = ReviewBot.ReviewBot.check_one_request(self, req)

        # check if factory-source is already a reviewer
        if self.add_factory_source:
            for r in req.reviews:
                if r.by_user == 'factory-source':
                    self.add_factory_source = False
                    self.logger.debug("factory-source already is a reviewer")
                    break

        if self.add_factory_source:
            self.logger.debug("%s needs review by factory-source"%req.reqid)
            if self.add_review(req, by_user =  "factory-source") != True:
                ret = None

        if self.needs_maintainer_review:
            for p in self.needs_maintainer_review:
                self.add_devel_project_review(req, p)

        return ret

if __name__ == "__main__":
    app = ReviewBot.CommandLineInterface()
    app.clazz = MaintenanceChecker
    sys.exit( app.main() )

# vim: sw=4 et
