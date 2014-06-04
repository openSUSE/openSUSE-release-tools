# Copyright (C) 2014 SUSE Linux Products GmbH
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

import urllib2
from xml.etree import cElementTree as ET

from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl

from osclib.stagingapi import StagingAPI


class CheckRepo(object):

    def __init__(self, apiurl):
        """CheckRepo constructor."""
        self.apiurl = apiurl
        self.staging = StagingAPI(apiurl)

        # grouped = { id: staging, }
        self.grouped = {}
        # groups = { staging: [ids,], }
        self.groups = {}
        self._staging()

    def _staging(self):
        """
        Preload the groups of related request associated by the same
        staging project.
        """
        for project in self.staging.get_staging_projects():
            # Get all the requests identifier for the project
            requests = self.staging.get_prj_pseudometa(project)['requests']
            requests = [req['id'] for req in requests]

            # Note: Originally we recover also the request returned by
            # list_requests_in_prj().  I guest that if the staging
            # project is working properly, this method do not add any
            # new request to the list.
            if requests:
                self.groups[project] = requests
                self.grouped.update({req: project for req in requests})

    def change_review_state(self, request_id, newstate, message=''):
        """Based on osc/osc/core.py. Fixed 'by_user'."""
        query = {
            'cmd': 'changereviewstate',
            'newstate': newstate,
            # XXX TODO - We force the user here, check if the user
            # expressed in .oscrc (with the password stored) have
            # rights to become this user.
            'by_user': 'factory-repo-checker',
        }

        code = 404
        url = makeurl(self.apiurl, ['request', str(request_id)], query=query)
        try:
            root = ET.parse(http_POST(url, data=message)).getroot()
            code = root.attrib['code']
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return code

    def get_request(self, request_id):
        """Get a request XML onject."""
        request = None
        try:
            url = makeurl(self.apiurl, ('request', str(request_id)))
            request = ET.parse(http_GET(url)).getroot()
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return request

    def pending_requests(self):
        """Search pending requests to review."""
        requests = []
        where = "@by_user='factory-repo-checker'+and+@state='new'"
        try:
            url = makeurl(self.apiurl, ('search', 'request'),
                          "match=state/@name='review'+and+review[%s]" % where)
            root = ET.parse(http_GET(url)).getroot()
            requests = root.findall('request')
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return requests
