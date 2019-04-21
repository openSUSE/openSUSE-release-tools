import unittest

import vcr

from . import vcrhelpers

import os.path
from osc import oscerr
import osc.conf
from osclib.cache import Cache
from osclib.cache_manager import CacheManager
from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.select_command import SelectCommand
from osclib.stagingapi import StagingAPI
import logging

from mock import MagicMock

my_vcr = vcr.VCR(cassette_library_dir='tests/fixtures/vcr/select')

class TestSelect(unittest.TestCase):

    @my_vcr.use_cassette
    def test_old_frozen(self):
        wf = vcrhelpers.StagingWorkflow()
        wf.api.prj_frozen_enough = MagicMock(return_value=False)

        # check it won't allow selecting
        staging = wf.create_staging('Old')
        self.assertEqual(False, SelectCommand(wf.api, staging.name).perform(['gcc']))

    @my_vcr.use_cassette
    def test_select_comments(self):
        wf = vcrhelpers.StagingWorkflow()
        wf.setup_rings()

        staging_b = wf.create_staging('B', freeze=True)

        c_api = CommentAPI(wf.api.apiurl)
        comments = c_api.get_comments(project_name=staging_b.name)

        r1 = wf.create_submit_request('devel:wine', 'wine')
        r2 = wf.create_submit_request('devel:gcc', 'gcc')

        # First select
        self.assertEqual(True, SelectCommand(wf.api, staging_b.name).perform(['gcc', 'wine']))
        first_select_comments = c_api.get_comments(project_name=staging_b.name)
        last_id = sorted(first_select_comments.keys())[-1]
        first_select_comment = first_select_comments[last_id]
        # Only one comment is added
        self.assertEqual(len(first_select_comments), len(comments) + 1)
        # With the right content
        expected = 'request#{} for package gcc submitted by Admin'.format(r2.reqid)
        self.assertTrue(expected in first_select_comment['comment'])

        # Second select
        r3 = wf.create_submit_request('devel:gcc', 'gcc8')
        self.assertEqual(True, SelectCommand(wf.api, staging_b.name).perform(['gcc8']))
        second_select_comments = c_api.get_comments(project_name=staging_b.name)
        last_id = sorted(second_select_comments.keys())[-1]
        second_select_comment = second_select_comments[last_id]
        # The number of comments increased by one
        self.assertEqual(len(second_select_comments) - 1, len(first_select_comments))
        self.assertNotEqual(second_select_comment['comment'], first_select_comment['comment'])
        # The new comments contains new, but not old
        self.assertFalse('request#{} for package gcz submitted by Admin'.format(r2.reqid) in second_select_comment['comment'])
        self.assertTrue('added request#{} for package gcc8 submitted by Admin'.format(r3.reqid) in second_select_comment['comment'])

    @my_vcr.use_cassette
    def test_no_matches(self):
        wf = vcrhelpers.StagingWorkflow()

        staging = wf.create_staging('N', freeze=True)

        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(wf.api, staging.name).perform(['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    @my_vcr.use_cassette
    def test_selected(self):
        wf = vcrhelpers.StagingWorkflow()

        wf.setup_rings()
        staging = wf.create_staging('S', freeze=True)

        request = wf.create_submit_request('devel:wine', 'wine')

        ret = SelectCommand(wf.api, staging.name).perform(['wine'])
        self.assertEqual(True, ret)
