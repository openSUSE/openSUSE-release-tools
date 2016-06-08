# Copyright (C) 2015 SUSE Linux GmbH
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

import sys
import unittest
import httpretty

from obs import APIURL
from obs import OBS
from osclib.conf import Config
from osclib.stagingapi import StagingAPI


PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
else:
    string_types = basestring,


class TestApiCalls(unittest.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    def setUp(self):
        """
        Initialize the configuration
        """

        self.obs = OBS()
        Config('openSUSE:Factory')
        self.api = StagingAPI(APIURL, 'openSUSE:Factory')

    def tearDown(self):
        """Clean internal cache"""
        if hasattr(self.api, '_invalidate_all'):
            self.api._invalidate_all()

    def test_ring_packages(self):
        """
        Validate the creation of the rings.
        """
        # our content in the XML files
        # test content for listonly ie. list command
        ring_packages = {
            'elem-ring-0': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-1': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-mini': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-2': 'openSUSE:Factory:Rings:2-TestDVD',
            'git': 'openSUSE:Factory:Rings:2-TestDVD',
            'wine': 'openSUSE:Factory:Rings:1-MinimalX',
        }
        self.assertEqual(ring_packages, self.api.ring_packages_for_links)

        # test content for real usage
        ring_packages = {
            'elem-ring-0': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-1': 'openSUSE:Factory:Rings:1-MinimalX',
            'elem-ring-mini': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-2': 'openSUSE:Factory:Rings:2-TestDVD',
            'git': 'openSUSE:Factory:Rings:2-TestDVD',
            'wine': 'openSUSE:Factory:Rings:1-MinimalX',
        }
        self.assertEqual(ring_packages, self.api.ring_packages)

    @unittest.skip("no longer approving non-ring packages")
    def test_dispatch_open_requests(self):
        """
        Test dispatching and closure of non-ring packages
        """

        # Get rid of open requests
        self.api.dispatch_open_requests()
        # Check that we tried to close it
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'changereviewstate'])
        # Try it again
        self.api.dispatch_open_requests()
        # This time there should be nothing to close
        self.assertEqual(httpretty.last_request().method, 'GET')

    def test_pseudometa_get_prj(self):
        """
        Test getting project metadata from YAML in project description
        """

        # Try to get data from project that has no metadata
        data = self.api.get_prj_pseudometa('openSUSE:Factory:Staging:A')
        # Should be empty, but contain structure to work with
        self.assertEqual(data, {'requests': []})
        # Add some sample data
        rq = {'id': '123', 'package': 'test-package'}
        data['requests'].append(rq)
        # Save them and read them back
        self.api.set_prj_pseudometa('openSUSE:Factory:Staging:A', data)
        test_data = self.api.get_prj_pseudometa('openSUSE:Factory:Staging:A')
        # Verify that we got back the same data
        self.assertEqual(data, test_data)

    def test_list_projects(self):
        """
        List projects and their content
        """

        # Prepare expected results
        data = []
        for prj in self.obs.staging_project:
            data.append('openSUSE:Factory:Staging:' + prj)

        # Compare the results
        self.assertEqual(data, self.api.get_staging_projects())

    def test_open_requests(self):
        """
        Test searching for open requests
        """

        requests = []

        # get the open requests
        requests = self.api.get_open_requests()

        # Compare the results, we only care now that we got 1 of them not the content
        self.assertEqual(1, len(requests))

    def test_get_package_information(self):
        """
        Test if we get proper project, name and revision from the staging informations
        """

        package_info = {
            'dir_srcmd5': '751efeae52d6c99de48164088a33d855',
            'project': 'home:Admin',
            'rev': '7b98ac01b8071d63a402fa99dc79331c',
            'srcmd5': '7b98ac01b8071d63a402fa99dc79331c',
            'package': 'wine'
        }

        # Compare the results, we only care now that we got 2 of them not the content
        self.assertEqual(
            package_info,
            self.api.get_package_information('openSUSE:Factory:Staging:B', 'wine'))

    def test_request_id_package_mapping(self):
        """
        Test whether we can get correct id for sr in staging project
        """

        prj = 'openSUSE:Factory:Staging:B'
        # Get rq
        num = self.api.get_request_id_for_package(prj, 'wine')
        self.assertEqual(333, num)
        # Get package name
        self.assertEqual('wine', self.api.get_package_for_request_id(prj, num))

    def test_rm_from_prj(self):
        prj = 'openSUSE:Factory:Staging:B'
        pkg = 'wine'

        full_name = prj + '/' + pkg

        # Verify package is there
        self.assertTrue(full_name in self.obs.links)

        # Get rq number
        num = self.api.get_request_id_for_package(prj, pkg)

        # Delete the package
        self.api.rm_from_prj(prj, package='wine')

        # Verify package is not there
        self.assertTrue(full_name not in self.obs.links)

        # RQ is gone
        self.assertEqual(None, self.api.get_request_id_for_package(prj, pkg))
        self.assertEqual(None, self.api.get_package_for_request_id(prj, num))

        # Verify that review is closed
        self.assertEqual('accepted', self.obs.requests[str(num)]['review'])
        self.assertEqual('new', self.obs.requests[str(num)]['request'])

    def test_rm_from_prj_2(self):
        # Try the same with request number
        prj = 'openSUSE:Factory:Staging:B'
        pkg = 'wine'

        full_name = prj + '/' + pkg

        # Get rq number
        num = self.api.get_request_id_for_package(prj, pkg)

        # Delete the package
        self.api.rm_from_prj(prj, request_id=num)

        # Verify package is not there
        self.assertTrue(full_name not in self.obs.links)

        # RQ is gone
        self.assertEqual(None, self.api.get_request_id_for_package(prj, pkg))
        self.assertEqual(None, self.api.get_package_for_request_id(prj, num))

        # Verify that review is closed
        self.assertEqual('accepted', self.obs.requests[str(num)]['review'])
        self.assertEqual('new', self.obs.requests[str(num)]['request'])

    def test_add_sr(self):
        prj = 'openSUSE:Factory:Staging:A'
        rq = '123'

        # Running it twice shouldn't change anything
        for i in range(2):
            # Add rq to the project
            self.api.rq_to_prj(rq, prj)
            # Verify that review is there
            self.assertEqual('new', self.obs.requests[rq]['review'])
            self.assertEqual('review', self.obs.requests[rq]['request'])
            self.assertEqual(self.api.get_prj_pseudometa('openSUSE:Factory:Staging:A'),
                             {'requests': [{'id': 123, 'package': 'gcc', 'author': 'Admin'}]})

    def test_create_package_container(self):
        """Test if the uploaded _meta is correct."""

        self.api.create_package_container('openSUSE:Factory:Staging:B', 'wine')
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title/><description/></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

        self.api.create_package_container('openSUSE:Factory:Staging:B', 'wine', disable_build=True)
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title /><description /><build><disable /></build></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

    def test_review_handling(self):
        """Test whether accepting/creating reviews behaves correctly."""

        # Add review
        self.api.add_review('123', by_project='openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'addreview'])
        # Try to readd, should do anything
        self.api.add_review('123', by_project='openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'GET')
        # Accept review
        self.api.set_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'changereviewstate'])
        # Try to accept it again should do anything
        self.api.set_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'GET')
        # But we should be able to reopen it
        self.api.add_review('123', by_project='openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'addreview'])

    def test_prj_from_letter(self):

        # Verify it works
        self.assertEqual(self.api.prj_from_letter('openSUSE:Factory'), 'openSUSE:Factory')
        self.assertEqual(self.api.prj_from_letter('A'), 'openSUSE:Factory:Staging:A')

    def test_frozen_mtime(self):
        """Test frozen mtime."""

        # Testing frozen mtime
        self.assertTrue(self.api.days_since_last_freeze('openSUSE:Factory:Staging:A') > 8)
        self.assertTrue(self.api.days_since_last_freeze('openSUSE:Factory:Staging:B') < 1)

        # U == unfrozen
        self.assertTrue(self.api.days_since_last_freeze('openSUSE:Factory:Staging:U') > 1000)

    def test_frozen_enough(self):
        """Test frozen enough."""

        # Testing frozen mtime
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:B'), True)
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), False)

        # U == unfrozen
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:U'), False)

    def test_move(self):
        """Test package movement."""

        init_data = self.api.get_package_information('openSUSE:Factory:Staging:B', 'wine')
        self.api.move_between_project('openSUSE:Factory:Staging:B', 333, 'openSUSE:Factory:Staging:A')
        test_data = self.api.get_package_information('openSUSE:Factory:Staging:A', 'wine')
        self.assertEqual(init_data, test_data)
