import logging
from . import OBSLocal
from check_source import CheckSource
import random
import os

PROJECT = 'openSUSE:Factory'
SRC_PROJECT = 'devel:Fishing'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')
REVIEW_TEAM = 'reviewers-team'

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
        """Declines the request when it does not come from a devel project"""
        req_id = self.wf.create_submit_request(SRC_PROJECT, self.randomString('package')).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn('%s is not a devel project of %s' % (SRC_PROJECT, PROJECT), review.comment)

    def test_devel_project(self):
        """Accepts a request coming from a devel project"""
        self._setup_devel_project()

        # Set up the reviewers team
        self.wf.create_group(REVIEW_TEAM)
        self.wf.remote_config_set({ 'review-team': REVIEW_TEAM }, replace_all=False)

        req_id = self.wf.create_submit_request(SRC_PROJECT, 'blowfish').reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertReview(req_id, by_group=(REVIEW_TEAM, 'new'))

    def _setup_devel_project(self):
        devel_project = self.wf.create_project(SRC_PROJECT)
        devel_package = OBSLocal.Package('blowfish', project=devel_project)

        blowfish_spec = os.path.join(FIXTURES, 'packages', 'blowfish', 'blowfish.spec')
        with open(blowfish_spec) as f:
            devel_package.create_commit(filename='blowfish.spec', text=f.read())

        blowfish_changes = os.path.join(FIXTURES, 'packages', 'blowfish', 'blowfish.changes')
        with open(blowfish_changes) as f:
            devel_package.create_commit(filename='blowfish.changes', text=f.read())

        devel_package.create_file(filename='blowfish-1.tar.gz')
        target_package = OBSLocal.Package('blowfish', self.wf.projects['target'], devel_project=SRC_PROJECT)
