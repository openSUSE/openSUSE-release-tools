import sys
import unittest
import re

import osc.core

from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from lxml import etree as ET
from mock import MagicMock
from nose.tools import nottest
from . import OBSLocal

class TestApiCalls(OBSLocal.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    def setUp(self):
        super(TestApiCalls, self).setUp()
        self.wf = OBSLocal.StagingWorkflow()
        self.wf.setup_rings()
        self.staging_b = self.wf.create_staging('B')
        prj = self.staging_b.name

        self.winerq = self.wf.create_submit_request('devel:wine', 'wine')
        self.wf.api.rq_to_prj(self.winerq.reqid, prj)

        # Get rq number
        num = self.wf.api.get_request_id_for_package(prj, 'wine')
        self.assertEqual(str(num), self.winerq.reqid)
        self.assertIsNotNone(num)
        self.assertTrue(self.wf.api.item_exists(prj, 'wine'))

    def tearDown(self):
        del self.wf
        super(TestApiCalls, self).tearDown()

    def test_ring_packages(self):
        """
        Validate the creation of the rings.
        """

        curl = self.wf.create_package('target', 'curl')
        curl.create_file('curl.spec')
        curl.create_file('curl-mini.spec')
        cmini = self.wf.create_link(curl, target_project=self.wf.projects['target'], target_package='curl-mini')
        cring1 = self.wf.create_link(curl, target_project=self.wf.projects['ring1'], target_package='curl')
        cring0 = self.wf.create_link(cring1, target_project=self.wf.projects['ring0'], target_package='curl-mini')

        # test content for listonly ie. list command
        ring_packages = {
            'curl': 'openSUSE:Factory:Rings:0-Bootstrap',
            'curl-mini': 'openSUSE:Factory:Rings:0-Bootstrap',
            'wine': 'openSUSE:Factory:Rings:1-MinimalX',
        }
        self.assertEqual(ring_packages, self.wf.api.ring_packages_for_links)

        # test content for real usage
        ring_packages = {
            'curl': 'openSUSE:Factory:Rings:1-MinimalX',
            'curl-mini': 'openSUSE:Factory:Rings:0-Bootstrap',
            'wine': 'openSUSE:Factory:Rings:1-MinimalX',
        }
        self.assertEqual(ring_packages, self.wf.api.ring_packages)

    def test_list_projects(self):
        """
        List projects and their content
        """

        staging_a = self.wf.create_staging('A')

        # Prepare expected results
        data = [staging_a.name, self.staging_b.name]

        # Compare the results
        self.assertEqual(data, self.wf.api.get_staging_projects())

    def test_open_requests(self):
        """
        Test searching for open requests
        """
        self.wf.create_submit_request('devel:wine', 'wine2')

        requests = self.wf.api.get_open_requests()
        # Compare the results, we only care now that we got 1 of them not the content
        self.assertEqual(1, len(requests))

    def test_request_id_package_mapping(self):
        """
        Test whether we can get correct id for sr in staging project
        """
        prj = self.staging_b.name

        # Get rq
        num = self.wf.api.get_request_id_for_package(prj, 'wine')
        self.assertEqual(str(num), self.winerq.reqid)
        # Get package name
        self.assertEqual('wine', self.wf.api.get_package_for_request_id(prj, num))

    def test_rm_from_prj(self):
        # Delete the package
        self.wf.api.rm_from_prj(self.staging_b.name, package='wine')
        self.verify_wine_is_gone()

    def test_rm_from_prj_2(self):
        # Try the same with request number
        self.wf.api.rm_from_prj(self.staging_b.name, request_id=self.winerq.reqid)
        self.verify_wine_is_gone()

    def verify_wine_is_gone(self):
        prj = self.staging_b.name
        pkg = 'wine'
        num = self.winerq.reqid

        # Verify package is not there
        self.assertFalse(self.wf.api.item_exists(prj, pkg))

        # RQ is gone
        self.assertIsNone(self.wf.api.get_request_id_for_package(prj, pkg))
        self.assertIsNone(self.wf.api.get_package_for_request_id(prj, num))

        # Verify that review is closed
        rq = self.winerq.xml()
        xpath = "//review[@name='new' and @by_project='{}']".format(self.staging_b.name)
        self.assertIsNotNone(rq.xpath(xpath))


    def test_add_sr(self):
        # setup is already adding the request, we just verify
        prj = self.staging_b.name
        pkg = 'wine'
        num = self.winerq.reqid

        # Verify that review is there
        reviews = [{'by_group': 'factory-staging', 'state': 'accepted'},
                   {'by_project': 'openSUSE:Factory:Staging:B', 'state': 'new'}]
        self.assertEqual(self.winerq.reviews(), reviews)
        self.assertEqual(self.wf.api.packages_staged,
                {'wine': {'prj': 'openSUSE:Factory:Staging:B', 'rq_id': num}})

    def test_create_package_container(self):
        """Test if the uploaded _meta is correct."""

        self.wf.create_staging('B')
        self.wf.api.create_package_container('openSUSE:Factory:Staging:B', 'wine')

        url = self.wf.api.makeurl(['source', 'openSUSE:Factory:Staging:B', 'wine', '_meta'])
        self.assertEqual(osc.core.http_GET(url).read().decode('utf-8'), '<package name="wine" project="openSUSE:Factory:Staging:B">\n  <title/>\n  <description/>\n</package>\n')

        self.wf.api.create_package_container('openSUSE:Factory:Staging:B', 'wine', disable_build=True)
        m = '<package name="wine" project="openSUSE:Factory:Staging:B">\n  <title/>\n  <description/>\n  <build>\n    <disable/>\n  </build>\n</package>\n'
        self.assertEqual(osc.core.http_GET(url).read().decode('utf-8'), m)

    def test_review_handling(self):
        """Test whether accepting/creating reviews behaves correctly."""

        request = self.wf.create_submit_request('devel:wine', 'winetools')
        reviews = [{'state': 'new', 'by_group': 'factory-staging'}]
        self.assertEqual(request.reviews(), reviews)
        num = request.reqid

        # Add review
        self.wf.api.add_review(num, by_project='openSUSE:Factory:Staging:B')
        reviews.append({'by_project': 'openSUSE:Factory:Staging:B', 'state': 'new'})
        self.assertEqual(request.reviews(), reviews)

        # Try to readd, should not do anything
        self.wf.api.add_review(num, by_project='openSUSE:Factory:Staging:B')
        self.assertEqual(request.reviews(), reviews)

        # Accept review
        self.wf.api.set_review(num, 'openSUSE:Factory:Staging:B')
        reviews[1]['state'] = 'accepted'
        self.assertEqual(request.reviews(), reviews)

        # Try to accept it again should do anything
        self.wf.api.set_review(num, 'openSUSE:Factory:Staging:B')
        self.assertEqual(request.reviews(), reviews)

        # But we should be able to reopen it
        self.wf.api.add_review(num, by_project='openSUSE:Factory:Staging:B')
        reviews.append({'by_project': 'openSUSE:Factory:Staging:B', 'state': 'new'})
        self.assertEqual(request.reviews(), reviews)

    def test_prj_from_letter(self):

        # Verify it works
        self.assertEqual(self.wf.api.prj_from_letter('openSUSE:Factory'), 'openSUSE:Factory')
        self.assertEqual(self.wf.api.prj_from_letter('A'), 'openSUSE:Factory:Staging:A')

    def test_frozen_mtime(self):
        """Test frozen mtime."""

        self.wf.create_staging('A')

        # unfrozen
        self.assertTrue(self.wf.api.days_since_last_freeze('openSUSE:Factory:Staging:A') > 1000)

        # Testing frozen mtime
        self.wf.create_staging('B', freeze=True, rings=1)
        self.assertLess(self.wf.api.days_since_last_freeze('openSUSE:Factory:Staging:B'), 1)

        self.mock_project_meta()
        self.assertGreater(self.wf.api.days_since_last_freeze('openSUSE:Factory:Staging:B'), 8)

    def test_frozen_enough(self):
        """Test frozen enough."""

        # already has requests
        self.assertEqual(self.wf.api.prj_frozen_enough('openSUSE:Factory:Staging:B'), True)

        self.wf.create_staging('A')

        # Unfrozen
        self.assertEqual(self.wf.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), False)

        self.wf.create_staging('C', freeze=True, rings=1)
        self.assertEqual(self.wf.api.prj_frozen_enough('openSUSE:Factory:Staging:C'), True)

        self.mock_project_meta()
        self.assertEqual(self.wf.api.prj_frozen_enough('openSUSE:Factory:Staging:C'), False)

    def mock_project_meta(self):
        body = """<directory name="_project" rev="3" vrev="" srcmd5="9dd1ec5b77a9e953662eb32955e9066a">
              <entry name="_frozenlinks" md5="64127b7a5dabbca0ec2bf04cd04c9195" size="16" mtime="1555000000"/>
              <entry name="_meta" md5="cf6fb1eac1a676d6c3707303ae2571ad" size="162" mtime="1555945413"/>
            </directory>"""

        self.wf.api._fetch_project_meta = MagicMock(return_value=body)

    @nottest # TODO
    def test_move(self):
        """Test package movement."""

        staging_a = self.wf.create_staging('A')

        self.assertTrue(self.wf.api.item_exists('openSUSE:Factory:Staging:B', 'wine'))
        self.assertFalse(self.wf.api.item_exists('openSUSE:Factory:Staging:A', 'wine'))
        self.wf.api.move_between_project('openSUSE:Factory:Staging:B', self.winerq.reqid, 'openSUSE:Factory:Staging:A')
        self.assertTrue(self.wf.api.item_exists('openSUSE:Factory:Staging:A', 'wine'))
        self.assertFalse(self.wf.api.item_exists('openSUSE:Factory:Staging:B', 'wine'))
