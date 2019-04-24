import unittest

from osclib.accept_command import AcceptCommand
from osclib.select_command import SelectCommand
from osclib.conf import Config
from osclib.comments import CommentAPI
from osclib.stagingapi import StagingAPI

from mock import MagicMock

import vcr
from . import vcrhelpers

my_vcr = vcr.VCR(cassette_library_dir='tests/fixtures/vcr/accept')

class TestAccept(unittest.TestCase):

    def setup_vcr(self):
        wf = vcrhelpers.StagingWorkflow()
        wf.setup_rings()

        self.c_api = CommentAPI(wf.api.apiurl)

        staging_b = wf.create_staging('B', freeze=True)
        self.prj = staging_b.name

        self.winerq = wf.create_submit_request('devel:wine', 'wine', text='Hallo World')
        self.assertEqual(True, SelectCommand(wf.api, self.prj).perform(['wine']))
        self.comments = self.c_api.get_comments(project_name=self.prj)
        self.assertGreater(len(self.comments), 0)
        return wf

    @my_vcr.use_cassette
    def test_accept_comments(self):
        wf = self.setup_vcr()

        self.assertEqual(True, AcceptCommand(wf.api).perform(self.prj))

        # Comments are cleared up
        accepted_comments = self.c_api.get_comments(project_name=self.prj)
        self.assertEqual(len(accepted_comments), 0)

    @my_vcr.use_cassette
    def test_accept_final_comment(self):
        wf = self.setup_vcr()

        # snipe out cleanup to see the comments before the final countdown
        wf.api.staging_deactivate = MagicMock(return_value=True)

        self.assertEqual(True, AcceptCommand(wf.api).perform(self.prj))

        comments = self.c_api.get_comments(project_name=self.prj)
        self.assertGreater(len(comments), len(self.comments))

        # check which id was added
        new_id = (set(comments.keys()) - set(self.comments.keys())).pop()
        comment = comments[new_id]['comment']
        self.assertEqual('Project "{}" accepted. The following packages have been submitted to openSUSE:Factory: wine.'.format(self.prj), comment)
