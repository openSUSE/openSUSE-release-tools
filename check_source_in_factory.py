#!/usr/bin/python

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
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.factory = [ "openSUSE:Factory" ]
        self.review_messages = { 'accepted' : 'ok', 'declined': 'the package needs to be accepted in Factory first' }
        self.history_limit = 5

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        super(FactorySourceChecker, self).check_source_submission(src_project, src_package, src_rev, target_project, target_package)
        src_srcinfo = self.get_sourceinfo(src_project, src_package, src_rev)
        if src_srcinfo is None:
            # source package does not exist?
            # handle here to avoid crashing on the next line
            self.logger.info("Could not get source info for %s/%s@%s" % (src_project, src_package, src_rev))
            return False
        projects = self._package_get_upstream_projects(target_package)
        if projects is None:
            self.logger.error("no upstream project found for {}, can't check".format(target_package))
            return False

        self.review_messages['declined'] = 'the package needs to be accepted in {} first'.format(' or '.join(projects))
        for project in projects:
            self.logger.info("Checking in project %s" % project)
            good = self._check_matching_srcmd5(project, target_package, src_srcinfo.verifymd5, self.history_limit)
            if good:
                self.logger.info("{} is in {}".format(target_package, project))
                return good

            good = self._check_requests(project, target_package, src_srcinfo.verifymd5)
            if good:
                self.logger.info("{} already reviewed for {}".format(target_package, project))

        if not good:
            self.logger.info('{} failed source submission check'.format(target_package))

        return good

    def _package_get_upstream_projects(self, package):
        """ return list of projects where the specified package is supposed to come
        from. Either by lookup table or self.factory """
        projects = []
        for prj in self.factory:
            r = self.lookup.get(prj, package)
            if r:
                projects.append(r)

        if not projects:
            projects = self.factory

        return projects

    def _check_requests(self, project, package, rev):
        self.logger.debug("checking requests")
        prjprefix = ''
        apiurl = self.apiurl
        sr = 'sr'
        try:
            if self.config.project_namespace_api_map:
                for prefix, url, srprefix in self.config.project_namespace_api_map:
                    if project.startswith(prefix):
                        apiurl = url
                        prjprefix = prefix
                        project = project[len(prefix):]
                        sr = srprefix
                        break
            requests = osc.core.get_request_list(apiurl, project, package, None, ['new', 'review'], 'submit')
        except (urllib2.HTTPError, urllib2.URLError):
            self.logger.error("caught exception while checking %s/%s", project, package)
            return None

        def srref(reqid):
            return '#'.join((sr, reqid))

        for req in requests:
            for a in req.actions:
                si = self.get_sourceinfo(prjprefix + a.src_project, a.src_package, a.src_rev)
                self.logger.debug("rq %s: %s/%s@%s"%(req.reqid, prjprefix + a.src_project, a.src_package, si.verifymd5))
                if si.verifymd5 == rev:
                    if req.state.name == 'new':
                        self.logger.info("%s ok", srref(req.reqid))
                        return True
                    elif req.state.name == 'review':
                        self.logger.debug("%s still in review", srref(req.reqid))
                        if not req.reviews:
                            self.logger.error("%s in state review but no reviews?", srref(req.reqid))
                            return False
                        for r in req.reviews:
                            if r.state == 'new':
                                if r.by_project and r.by_project.startswith('openSUSE:Factory:Staging:'):
                                    self.logger.info("%s review by %s ok", srref(req.reqid), r.by_project)
                                    continue

                                if r.by_user == 'repo-checker':
                                    self.logger.info('%s review by %s ok', srref(req.reqid), r.by_user)
                                    continue

                            if r.state != 'accepted':
                                if r.by_user:
                                    self.logger.info("%s waiting for review by %s", srref(req.reqid), r.by_user)
                                elif r.by_group:
                                    self.logger.info("%s waiting for review by %s", srref(req.reqid), r.by_group)
                                elif r.by_project:
                                    if r.by_package:
                                        self.logger.info("%s waiting for review by %s/%s", srref(req.reqid), r.by_project, r.by_package)
                                    else:
                                        self.logger.info("%s waiting for review by %s", srref(req.reqid), r.by_project)
                                return None
                        return True
                    else:
                        self.logger.error("%s in state %s not expected", srref(req.reqid), req.state.name)
                        return None
                else:
                    self.logger.info("%s to %s has different sources", srref(req.reqid), project)
        return False

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = FactorySourceChecker

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)
        parser.add_option("--factory", metavar="project", action="append",
                          help=("Project to check source against. Use multiple times to check more than one project. "
                                "[default: openSUSE:Factory]"))
        parser.add_option("--lookup", metavar="project", help="use lookup file")
        parser.add_option("--limit", metavar="limit", help="how many revisions back to check. [default: 5]")

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.factory:
            bot.factory = self.options.factory
        if self.options.lookup:
            bot.parse_lookup(self.options.lookup)
        if self.options.limit:
            bot.history_limit = self.options.limit

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

