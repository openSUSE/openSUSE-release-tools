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
        Config(APIURL, 'openSUSE:Factory')
        self.api = StagingAPI(APIURL, 'openSUSE:Factory')

    def test_old_frozen(self):
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), False)
        # check it won't allow selecting
        self.assertEqual(False, SelectCommand(self.api, 'openSUSE:Factory:Staging:A').perform(['gcc']))

    def test_select_comments(self):
        c_api = CommentAPI(self.api.apiurl)
        staging_b = 'openSUSE:Factory:Staging:B'
        comments = c_api.get_comments(project_name=staging_b)

        # First select
        self.assertEqual(True, SelectCommand(self.api, staging_b).perform(['gcc', 'wine']))
        first_select_comments = c_api.get_comments(project_name=staging_b)
        last_id = sorted(first_select_comments.keys())[-1]
        first_select_comment = first_select_comments[last_id]
        # Only one comment is added
        self.assertEqual(len(first_select_comments), len(comments) + 1)
        # With the right content
        self.assertTrue('request#123 for package gcc submitted by Admin' in first_select_comment['comment'])

        # Second select
        self.assertEqual(True, SelectCommand(self.api, staging_b).perform(['puppet']))
        second_select_comments = c_api.get_comments(project_name=staging_b)
        last_id = sorted(second_select_comments.keys())[-1]
        second_select_comment = second_select_comments[last_id]
        # The number of comments increased by one
        self.assertEqual(len(second_select_comments) - 1, len(first_select_comments))
        self.assertNotEqual(second_select_comment['comment'], first_select_comment['comment'])
        # The new comments contains new, but not old
        self.assertFalse('request#123 for package gcc submitted by Admin' in second_select_comment['comment'])
        self.assertTrue('added request#321 for package puppet submitted by Admin' in second_select_comment['comment'])

    def test_no_matches(self):
        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.api, 'openSUSE:Factory:Staging:B').perform(['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    def test_selected(self):
        # make sure the project is frozen recently for other tests

        ret = SelectCommand(self.api, 'openSUSE:Factory:Staging:B').perform(['wine'])
        self.assertEqual(True, ret)
