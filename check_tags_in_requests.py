#!/usr/bin/python

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
        super(TagChecker, self).__init__(*args, **kwargs)
        self.factory = [ "openSUSE:Factory" ]
        self.review_messages['declined'] = """
(This is a script, so report bugs)

The project you submitted to requires a bug tracker ID marked in the
.changes file. OBS supports several patterns, see
$ osc api /issue_trackers

See also https://en.opensuse.org/openSUSE:Packaging_Patches_guidelines#Current_set_of_abbreviations

Note that not all of the tags listed there are necessarily supported
by OBS on which this bot relies.
"""
        self.request_default_return = True

    def isNewPackage(self, tgt_project, tgt_package):
        try:
            self.logger.debug("package_meta %s %s/%s" % (self.apiurl, tgt_project, tgt_package))
            osc.core.show_package_meta(self.apiurl, tgt_project, tgt_package)
        except (HTTPError, URLError):
            return True
        return False

    def checkTagInRequest(self, req, a):
        u = osc.core.makeurl(self.apiurl,
                             ['source', a.src_project, a.src_package],
                             {'cmd': 'diff',
                              'onlyissues': '1',
                              'view': 'xml',
                              'opackage': a.tgt_package,
                              'oproject': a.tgt_project,
                              'expand': '1',
                              'rev': a.src_rev})
        try:
            f = osc.core.http_POST(u)
        except (HTTPError, URLError):
            if self.isNewPackage(a.tgt_project, a.tgt_package):
                self.review_messages['accepted'] = 'New package'
                return True

            self.logger.debug('error loading diff, assume transient error')
            return None

        xml = ET.parse(f)
        issues = len(xml.findall('./issues/issue'))
        removed = len(xml.findall('./issues/issue[@state="removed"]'))
        if issues == 0:
            self.logger.debug("reject: diff contains no tags")
            return False
        if removed > 0:
            self.review_messages['accepted'] = 'Warning: {} issues reference(s) removed'.format(removed)
            return True
        return True

    def checkTagNotRequired(self, req, a):
        # if there is no diff, no tag is required
        diff = osc.core.request_diff(self.apiurl, req.reqid)
        if not diff:
            return True

        # 1) A tag is not required only if the package is
        # already in Factory with the same revision,
        # and the package is being introduced, not updated
        # 2) A new package must have an issue tag
        factory_checker = check_source_in_factory.FactorySourceChecker(apiurl=self.apiurl,
                                                                       dryrun=self.dryrun,
                                                                       logger=self.logger,
                                                                       user=self.review_user,
                                                                       group=self.review_group)
        factory_checker.factory = self.factory
        factory_ok = factory_checker.check_source_submission(a.src_project, a.src_package, a.src_rev,
                                                             a.tgt_project, a.tgt_package)
        return factory_ok

    def checkTagNotRequiredOrInRequest(self, req, a):
        tags = self.checkTagInRequest(req, a)
        if tags is True:
            return True

        return self.checkTagNotRequired(req, a)

    def check_action_submit(self, req, a):
        return self.checkTagNotRequiredOrInRequest(req, a)

    def check_action_maintenance_incident(self, req, a):
        return self.checkTagInRequest(req, a)

    def check_action_maintenance_release(self, req, a):
        return self.checkTagInRequest(req, a)


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = TagChecker

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)
        parser.add_option("--factory", metavar="project", help="the openSUSE Factory project")

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.factory:
            bot.factory = self.options.factory

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

