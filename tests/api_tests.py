from __future__ import print_function

import sys
import unittest
import httpretty
import re

import osc.core

from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from xml.etree import cElementTree as ET
from mock import MagicMock
from . import OBSLocal

class TestApiCalls(OBSLocal.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    def test_ring_packages(self):
        """
        Validate the creation of the rings.
        """

        wf = OBSLocal.StagingWorkflow()
        wf.setup_rings()

        curl = wf.create_package('target', 'curl')
        curl.create_file('curl.spec')
        curl.create_file('curl-mini.spec')
        cmini = wf.create_link(curl, target_project=wf.projects['target'], target_package='curl-mini')
        cring1 = wf.create_link(curl, target_project=wf.projects['ring1'], target_package='curl')
        cring0 = wf.create_link(cring1, target_project=wf.projects['ring0'], target_package='curl-mini')

        # test content for listonly ie. list command
        ring_packages = {
            'curl': 'openSUSE:Factory:Rings:0-Bootstrap',
            'curl-mini': 'openSUSE:Factory:Rings:0-Bootstrap',
            'wine': 'openSUSE:Factory:Rings:1-MinimalX',
        }
        self.assertEqual(ring_packages, wf.api.ring_packages_for_links)

        # test content for real usage
        ring_packages = {
            'curl': 'openSUSE:Factory:Rings:1-MinimalX',
            'curl-mini': 'openSUSE:Factory:Rings:0-Bootstrap',
            'wine': 'openSUSE:Factory:Rings:1-MinimalX',
        }
        self.assertEqual(ring_packages, wf.api.ring_packages)

    def test_pseudometa_get_prj(self):
        """
        Test getting project metadata from YAML in project description
        """

        wf = self.setup_vcr()
        wf.create_staging('A')

        # Try to get data from project that has no metadata
        data = wf.api.get_prj_pseudometa('openSUSE:Factory:Staging:A')
        # Should be empty, but contain structure to work with
        self.assertEqual(data, {'requests': []})
        # Add some sample data
        rq = {'id': '123', 'package': 'test-package'}
        data['requests'].append(rq)
        # Save them and read them back
        wf.api.set_prj_pseudometa('openSUSE:Factory:Staging:A', data)
        test_data = wf.api.get_prj_pseudometa('openSUSE:Factory:Staging:A')
        # Verify that we got back the same data
        self.assertEqual(data, test_data)

    def test_list_projects(self):
        """
        List projects and their content
        """

        wf = self.setup_vcr()
        staging_a = wf.create_staging('A')

        # Prepare expected results
        data = [staging_a.name, self.staging_b.name]

        # Compare the results
        self.assertEqual(data, wf.api.get_staging_projects())

    def test_open_requests(self):
        """
        Test searching for open requests
        """
        wf = OBSLocal.StagingWorkflow()
        wf.create_submit_request('devel:wine', 'wine')

        # get the open requests
        requests = wf.api.get_open_requests()

        # Compare the results, we only care now that we got 1 of them not the content
        self.assertEqual(1, len(requests))

    def test_request_id_package_mapping(self):
        """
        Test whether we can get correct id for sr in staging project
        """

        wf = self.setup_vcr()
        prj = self.staging_b.name

        # Get rq
        num = wf.api.get_request_id_for_package(prj, 'wine')
        self.assertEqual(str(num), self.winerq.reqid)
        # Get package name
        self.assertEqual('wine', wf.api.get_package_for_request_id(prj, num))

    def setup_vcr(self):
        wf = OBSLocal.StagingWorkflow()
        wf.setup_rings()
        self.staging_b = wf.create_staging('B')
        prj = self.staging_b.name

        self.winerq = wf.create_submit_request('devel:wine', 'wine')
        wf.api.rq_to_prj(self.winerq.reqid, prj)

        # Get rq number
        num = wf.api.get_request_id_for_package(prj, 'wine')
        self.assertEqual(str(num), self.winerq.reqid)
        self.assertIsNotNone(num)
        self.assertTrue(wf.api.item_exists(prj, 'wine'))

        return wf

    def test_rm_from_prj(self):
        wf = self.setup_vcr()

        # Delete the package
        wf.api.rm_from_prj(self.staging_b.name, package='wine')
        self.verify_wine_is_gone(wf)

    def test_rm_from_prj_2(self):
        wf = self.setup_vcr()

        # Try the same with request number
        wf.api.rm_from_prj(self.staging_b.name, request_id=self.winerq.reqid)
        self.verify_wine_is_gone(wf)

    def verify_wine_is_gone(self, wf):
        prj = self.staging_b.name
        pkg = 'wine'
        num = self.winerq.reqid

        # Verify package is not there
        self.assertFalse(wf.api.item_exists(prj, pkg))

        # RQ is gone
        self.assertIsNone(wf.api.get_request_id_for_package(prj, pkg))
        self.assertIsNone(wf.api.get_package_for_request_id(prj, num))

        # Verify that review is closed
        rq = self.winerq.xml()
        self.assertIsNotNone(rq.find('.//state[@name="new"]'))

    def test_add_sr(self):
        wf = self.setup_vcr()

        prj = self.staging_b.name
        pkg = 'wine'
        num = self.winerq.reqid

        # Running it twice shouldn't change anything
        for i in range(2):
            # Add rq to the project
            wf.api.rq_to_prj(num, prj)
            # Verify that review is there
            reviews = [{'by_group': 'factory-staging', 'state': 'accepted'},
                       {'by_project': 'openSUSE:Factory:Staging:B', 'state': 'new'}]
            self.assertEqual(self.winerq.reviews(), reviews)
            self.assertEqual(wf.api.get_prj_pseudometa(prj),
                    {'requests': [{'id': int(num), 'package': 'wine', 'author': 'Admin', 'type': 'submit'}]})

    def test_create_package_container(self):
        """Test if the uploaded _meta is correct."""

        wf = OBSLocal.StagingWorkflow()
        wf.create_staging('B')
        wf.api.create_package_container('openSUSE:Factory:Staging:B', 'wine')

        url = wf.api.makeurl(['source', 'openSUSE:Factory:Staging:B', 'wine', '_meta'])
        self.assertEqual(osc.core.http_GET(url).read().decode('utf-8'), '<package name="wine" project="openSUSE:Factory:Staging:B">\n  <title/>\n  <description/>\n</package>\n')

        wf.api.create_package_container('openSUSE:Factory:Staging:B', 'wine', disable_build=True)
        m = '<package name="wine" project="openSUSE:Factory:Staging:B">\n  <title/>\n  <description/>\n  <build>\n    <disable/>\n  </build>\n</package>\n'
        self.assertEqual(osc.core.http_GET(url).read().decode('utf-8'), m)

    def test_review_handling(self):
        """Test whether accepting/creating reviews behaves correctly."""

        wf = self.setup_vcr()

        request = wf.create_submit_request('devel:wine', 'winetools')
        reviews = [{'state': 'new', 'by_group': 'factory-staging'}]
        self.assertEqual(request.reviews(), reviews)
        num = request.reqid

        # Add review
        wf.api.add_review(num, by_project='openSUSE:Factory:Staging:B')
        reviews.append({'by_project': 'openSUSE:Factory:Staging:B', 'state': 'new'})
        self.assertEqual(request.reviews(), reviews)

        # Try to readd, should not do anything
        wf.api.add_review(num, by_project='openSUSE:Factory:Staging:B')
        self.assertEqual(request.reviews(), reviews)

        # Accept review
        wf.api.set_review(num, 'openSUSE:Factory:Staging:B')
        reviews[1]['state'] = 'accepted'
        self.assertEqual(request.reviews(), reviews)

        # Try to accept it again should do anything
        wf.api.set_review(num, 'openSUSE:Factory:Staging:B')
        self.assertEqual(request.reviews(), reviews)

        # But we should be able to reopen it
        wf.api.add_review(num, by_project='openSUSE:Factory:Staging:B')
        reviews.append({'by_project': 'openSUSE:Factory:Staging:B', 'state': 'new'})
        self.assertEqual(request.reviews(), reviews)

    def test_prj_from_letter(self):

        wf = OBSLocal.StagingWorkflow()
        # Verify it works
        self.assertEqual(wf.api.prj_from_letter('openSUSE:Factory'), 'openSUSE:Factory')
        self.assertEqual(wf.api.prj_from_letter('A'), 'openSUSE:Factory:Staging:A')

    def test_frozen_mtime(self):
        """Test frozen mtime."""

        wf = OBSLocal.StagingWorkflow()
        wf.setup_rings()
        wf.create_staging('A')

        # unfrozen
        self.assertTrue(wf.api.days_since_last_freeze('openSUSE:Factory:Staging:A') > 1000)

        # Testing frozen mtime
        wf.create_staging('B', freeze=True, rings=1)
        self.assertLess(wf.api.days_since_last_freeze('openSUSE:Factory:Staging:B'), 1)
        self.mock_project_meta(wf)
        self.assertGreater(wf.api.days_since_last_freeze('openSUSE:Factory:Staging:B'), 8)

    def test_frozen_enough(self):
        """Test frozen enough."""

        wf = OBSLocal.StagingWorkflow()
        wf.setup_rings()
        wf.create_staging('A')

        # Unfrozen
        self.assertEqual(wf.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), False)

        wf.create_staging('B', freeze=True, rings=1)
        self.assertEqual(wf.api.prj_frozen_enough('openSUSE:Factory:Staging:B'), True)

        self.mock_project_meta(wf)
        self.assertEqual(wf.api.prj_frozen_enough('openSUSE:Factory:Staging:B'), False)

    def mock_project_meta(self, wf):
        body = """<directory name="_project" rev="3" vrev="" srcmd5="9dd1ec5b77a9e953662eb32955e9066a">
              <entry name="_frozenlinks" md5="64127b7a5dabbca0ec2bf04cd04c9195" size="16" mtime="1555000000"/>
              <entry name="_meta" md5="cf6fb1eac1a676d6c3707303ae2571ad" size="162" mtime="1555945413"/>
            </directory>"""

        wf.api._fetch_project_meta = MagicMock(return_value=body)

    def test_move(self):
        """Test package movement."""

        wf = self.setup_vcr()
        staging_a = wf.create_staging('A')

        self.assertTrue(wf.api.item_exists('openSUSE:Factory:Staging:B', 'wine'))
        self.assertFalse(wf.api.item_exists('openSUSE:Factory:Staging:A', 'wine'))
        wf.api.move_between_project('openSUSE:Factory:Staging:B', self.winerq.reqid, 'openSUSE:Factory:Staging:A')
        self.assertTrue(wf.api.item_exists('openSUSE:Factory:Staging:A', 'wine'))
        self.assertFalse(wf.api.item_exists('openSUSE:Factory:Staging:B', 'wine'))
