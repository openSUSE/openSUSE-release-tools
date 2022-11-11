#!/usr/bin/python3

# SPDX-License-Identifier: MIT

import sys
import re

from urllib.error import HTTPError

import osc.conf
import osc.core
import ReviewBot

http_GET = osc.core.http_GET


class CheckerBugowner(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.request_default_return = True
        self.override_allow = False

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s" % (src_project,
                                                src_package, src_rev, target_project, target_package))
        if src_package.startswith('patchinfo'):
            return True
        if self.exists_in(target_project, target_package):
            return True
        for line in self.request.description.splitlines():
            matched_package = None
            matched_maintainer = None
            m = re.match(r'\s*bugowner:\s*(\S+)\s*$', line)
            if m:
                matched_maintainer = m.group(1)
            m = re.match(r'\s*bugowner:\s(\S+)\s(\S+)\s*$', line)
            if m:
                matched_maintainer = m.group(2)
                matched_package = m.group(1)
            if not matched_maintainer:
                continue
            if matched_package and matched_package != target_package:
                continue
            if not self.valid_maintainer(matched_maintainer):
                self.review_messages['declined'] += f"\n{matched_maintainer} could not be found on this instance."
                return False
            return True
        self.review_messages['declined'] += f"\n{target_package } appears to be a new package and " + \
            "no matching 'bugowner:' line could be found in the request description. See https://confluence.suse.com/x/WgH2OQ"
        return False

    def existing_url(self, url):
        "Return False if url returns 404"
        try:
            osc.core.http_GET(url)
        except HTTPError as e:
            if e.code == 404:
                return False
        return True

    def valid_maintainer(self, maintainer):
        if maintainer.startswith('group:'):
            maintainer = maintainer.replace('group:', '')
            url = osc.core.makeurl(self.apiurl, ['group', maintainer])
            return self.existing_url(url)
        url = osc.core.makeurl(self.apiurl, ['person', maintainer])
        return self.existing_url(url)

    def exists_in(self, project, package):
        url = osc.core.makeurl(self.apiurl, ['source', project, package])
        return self.existing_url(url)


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = CheckerBugowner


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
