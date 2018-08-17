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
