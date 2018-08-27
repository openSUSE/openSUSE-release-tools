import unittest

from obs import APIURL
from obs import OBS
from osclib.accept_command import AcceptCommand
from osclib.conf import Config
from osclib.comments import CommentAPI
from osclib.stagingapi import StagingAPI


class TestAccept(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """
        self.obs = OBS()
        Config(APIURL, 'openSUSE:Factory')
        self.api = StagingAPI(APIURL, 'openSUSE:Factory')

    def test_accept_comments(self):
        c_api = CommentAPI(self.api.apiurl)
        staging_c = 'openSUSE:Factory:Staging:C'
        comments = c_api.get_comments(project_name=staging_c)

        # Accept staging C (containing apparmor and mariadb)
        self.assertEqual(True, AcceptCommand(self.api).perform(staging_c))

        # Comments are cleared up
        accepted_comments = c_api.get_comments(project_name=staging_c)
        self.assertNotEqual(len(comments), 0)
        self.assertEqual(len(accepted_comments), 0)

        # But the comment was written at some point
        self.assertEqual(len(self.obs.comment_bodies), 1)
        comment = self.obs.comment_bodies[0]
        self.assertTrue('The following packages have been submitted to openSUSE:Factory' in comment)
        self.assertTrue('apparmor' in comment)
        self.assertTrue('mariadb' in comment)
