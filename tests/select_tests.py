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

import unittest

from obs import APIURL
from obs import OBS
from osc import oscerr
from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.select_command import SelectCommand
from osclib.stagingapi import StagingAPI


class TestSelect(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """
        self.obs = OBS()
        Config('openSUSE:Factory')
        self.api = StagingAPI(APIURL, 'openSUSE:Factory')

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
        self.assertTrue('Request#123 for package gcc submitted by @Admin' in first_select_comment['comment'])

        # Second select
        self.assertEqual(True, SelectCommand(self.api).perform(staging_b, ['puppet']))
        second_select_comments = c_api.get_comments(project_name=staging_b)
        last_id = sorted(second_select_comments.keys())[-1]
        second_select_comment = second_select_comments[last_id]
        # The number of comments remains, but they are different
        self.assertEqual(len(second_select_comments), len(first_select_comments))
        self.assertNotEqual(second_select_comment['comment'], first_select_comment['comment'])
        # The new comments contents both old and new information
        self.assertTrue('Request#123 for package gcc submitted by @Admin' in second_select_comment['comment'])
        self.assertTrue('Request#321 for package puppet submitted by @Admin' in second_select_comment['comment'])

    def test_no_matches(self):
        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.api).perform('openSUSE:Factory:Staging:B', ['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    def test_selected(self):
        # make sure the project is frozen recently for other tests

        ret = SelectCommand(self.api).perform('openSUSE:Factory:Staging:B', ['wine'])
        self.assertEqual(True, ret)
