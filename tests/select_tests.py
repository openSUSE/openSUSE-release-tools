#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import sys
import unittest
import httpretty
import time

from obs import APIURL
from obs import OBS
from osc import oscerr
from osclib.select_command import SelectCommand
from oscs import StagingAPI
from osclib.comments import CommentAPI

class TestSelect(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """
        self.obs = OBS()
        self.api = StagingAPI(APIURL)

    def test_old_frozen(self):
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), False)
        # check it won't allow selecting
        self.assertEqual(False, SelectCommand(self.api).perform('openSUSE:Factory:Staging:A', ['gcc']))
        
    def test_select_comments(self):
        c_api = CommentAPI(self.api.apiurl)
        staging_b = 'openSUSE:Factory:Staging:B'
        comments = c_api.get_comments(project_name=staging_b)

        # First select
        self.assertEqual(True, SelectCommand(self.api).perform(staging_b, ['gcc', 'wine']))
        first_select_comments = c_api.get_comments(project_name=staging_b)
        last_id = sorted(first_select_comments.keys())[-1]
        first_select_comment = first_select_comments[last_id]
        # Only one comment is added
        self.assertEqual(len(first_select_comments), len(comments) + 1)
        # With the right content
        self.assertTrue('Request#123 for package gcc submitted by [AT]Admin' in first_select_comment['comment'])

        # Second select
        self.assertEqual(True, SelectCommand(self.api).perform(staging_b, ['puppet']))
        second_select_comments = c_api.get_comments(project_name=staging_b)
        last_id = sorted(second_select_comments.keys())[-1]
        second_select_comment = second_select_comments[last_id]
        # The number of comments remains, but they are different
        self.assertEqual(len(second_select_comments), len(first_select_comments))
        self.assertNotEqual(second_select_comment['comment'], first_select_comment['comment'])
        # The new comments contents both old and new information
        self.assertTrue('Request#123 for package gcc submitted by [AT]Admin' in second_select_comment['comment'])
        self.assertTrue('Request#321 for package puppet submitted by [AT]Admin' in second_select_comment['comment'])

    def test_no_matches(self):
        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.api).perform('openSUSE:Factory:Staging:B', ['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    def test_selected(self):
        # make sure the project is frozen recently for other tests

        ret = SelectCommand(self.api).perform('openSUSE:Factory:Staging:B', ['wine'])
        self.assertEqual(True, ret)
