import unittest
import os.path
from osc import oscerr
import osc.conf
from osclib.cache import Cache
from osclib.cache_manager import CacheManager
from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.core import package_list
from osclib.select_command import SelectCommand
from osclib.unselect_command import UnselectCommand
from osclib.stagingapi import StagingAPI
from osclib.memoize import memoize_session_reset
import logging

from mock import MagicMock
from . import OBSLocal

class TestSelect(unittest.TestCase):

    def setUp(self):
        self.wf = OBSLocal.StagingWorkflow()

    def tearDown(self):
        del self.wf
        super(TestSelect, self).tearDown()

    def test_old_frozen(self):
        self.wf.api.prj_frozen_enough = MagicMock(return_value=False)

        # check it won't allow selecting
        staging = self.wf.create_staging('Old')
        self.assertEqual(False, SelectCommand(self.wf.api, staging.name).perform(['gcc']))

    def test_select_comments(self):
        self.wf.setup_rings()

        staging_b = self.wf.create_staging('B', freeze=True)

        c_api = CommentAPI(self.wf.api.apiurl)
        comments = c_api.get_comments(project_name=staging_b.name)

        r1 = self.wf.create_submit_request('devel:wine', 'wine')
        r2 = self.wf.create_submit_request('devel:gcc', 'gcc')

        # First select
        self.assertEqual(True, SelectCommand(self.wf.api, staging_b.name).perform(['gcc', 'wine']))
        first_select_comments = c_api.get_comments(project_name=staging_b.name)
        last_id = sorted(first_select_comments.keys())[-1]
        first_select_comment = first_select_comments[last_id]
        # Only one comment is added
        self.assertEqual(len(first_select_comments), len(comments) + 1)
        # With the right content
        expected = 'request#{} for package gcc submitted by Admin'.format(r2.reqid)
        self.assertTrue(expected in first_select_comment['comment'])

        # Second select
        r3 = self.wf.create_submit_request('devel:gcc', 'gcc8')
        self.assertEqual(True, SelectCommand(self.wf.api, staging_b.name).perform(['gcc8']))
        second_select_comments = c_api.get_comments(project_name=staging_b.name)
        last_id = sorted(second_select_comments.keys())[-1]
        second_select_comment = second_select_comments[last_id]
        # The number of comments increased by one
        self.assertEqual(len(second_select_comments) - 1, len(first_select_comments))
        self.assertNotEqual(second_select_comment['comment'], first_select_comment['comment'])
        # The new comments contains new, but not old
        self.assertFalse('request#{} for package gcz submitted by Admin'.format(r2.reqid) in second_select_comment['comment'])
        self.assertTrue('added request#{} for package gcc8 submitted by Admin'.format(r3.reqid) in second_select_comment['comment'])

    def test_no_matches(self):
        staging = self.wf.create_staging('N', freeze=True)

        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.wf.api, staging.name).perform(['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    def test_selected(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('S', freeze=True)
        self.wf.create_submit_request('devel:wine', 'wine')

        ret = SelectCommand(self.wf.api, staging.name).perform(['wine'])
        self.assertEqual(True, ret)

    def test_select_multiple_spec(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True)

        project = self.wf.create_project('devel:gcc')
        package = OBSLocal.Package(name='gcc8', project=project)
        package.create_commit(filename='gcc8.spec')
        package.create_commit(filename='gcc8-tests.spec')
        self.wf.submit_package(package)

        ret = SelectCommand(self.wf.api, staging.name).perform(['gcc8'])
        self.assertEqual(True, ret)

        self.assertEqual(package_list(self.wf.apiurl, staging.name), ['gcc8', 'gcc8-tests'])
        uc = UnselectCommand(self.wf.api)
        self.assertIsNone(uc.perform(['gcc8']))

        # no stale links
        self.assertEqual([], package_list(self.wf.apiurl, staging.name))
