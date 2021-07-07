import logging
from . import OBSLocal
from check_source import CheckSource
import random

PROJECT = 'openSUSE:Factory'
SRC_PROJECT = 'devel:Fishing'

# NOTE: Since there is no documentation explaining the good practices for creating tests for a
# review bot, this test is created by mimicking parts of other existing tests. Most decisions are
# documented, but some rationales may be wrong. So don't take this as a model for creating
# future tests.

# Inherit from OBSLocal.Testcase since it provides many commodity methods for testing against
# a local testing instance of OBS
class TestCheckSource(OBSLocal.TestCase):
    def setUp(self):
        super(TestCheckSource, self).setUp()

        # Using OBSLocal.StagingWorkflow makes it easier to setup testing scenarios
        self.wf = OBSLocal.StagingWorkflow(PROJECT)
        self.bot_user = 'factory-auto'
        self.wf.create_user(self.bot_user)
        self.project = self.wf.create_project(PROJECT)
        # When creating a review, set the by_user to bot_user
        self.project.update_meta(reviewer={'users': [self.bot_user]})

        # Ensure different test runs operate in unique namespace.
        self.bot_name = '::'.join([type(self).__name__, str(random.getrandbits(8))])

        # StagingWorkflow creates reviews with the reviewer set to bot_user, so it's necessary to
        # configure our review bot to act upon such reviews
        self.review_bot = CheckSource(
            self.wf.apiurl, user=self.bot_user, logger=logging.getLogger(self.bot_name)
        )
        self.review_bot.bot_name = self.bot_name

    def tearDown(self):
        super().tearDown()
        del self.wf

    def test_no_devel_project(self):
        req_id = self.wf.create_submit_request(SRC_PROJECT, self.randomString('package')).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn('%s is not a devel project of %s' % (SRC_PROJECT, PROJECT), review.comment)
