# Copyright (C) 2016,2017 SUSE LLC
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import json
import osc
import urllib2

class PrioCommand(object):
    def __init__(self, api):
        self.api = api

    def _setprio(self, project):
        """
        Set prios for requests that are still in review
        :param project: project to check

        """

        # XXX taking name verbatim would produce null byte error
        # https://github.com/openSUSE/open-build-service/issues/2493
        message = 'raising priority for %s'%str(project['name'])
        for r in project['missing_reviews']:
            reqid = str(r['request'])
            req = osc.core.get_request(self.api.apiurl, reqid)
            priority = req.priority
            if priority is None:
                priority = 'important'
                query = { 'cmd': 'setpriority', 'priority': priority }
                url = osc.core.makeurl(self.api.apiurl, ['request', reqid], query)
                print reqid, message
                try:
                    osc.core.http_POST(url, data=message)
                    print reqid, r['by'], priority
                except urllib2.HTTPError, e:
                    print e


    def perform(self, project=None):
        """
        Check one staging project verbosibly or all of them at once
        :param project: project to check, None for all
        """

        query = {'format': 'json'}
        path = [ 'project', 'staging_projects', self.api.project ]
        if project:
            path += project
        url = self.api.makeurl(path, query=query)
        info = json.load(self.api.retried_GET(url))
        if not project:
            for prj in info:
                if not prj['selected_requests']:
                    continue
                self._setprio(prj)
        else:
            self._setprio(info)

        return True
