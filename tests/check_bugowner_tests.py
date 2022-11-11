import logging
from . import OBSLocal
from check_bugowner import CheckerBugowner
import pytest

PROJECT = "SLE:Next-SP"


@pytest.fixture
def default_config(request):
    wf = OBSLocal.FactoryWorkflow(PROJECT)
    project = wf.projects[PROJECT]

    request.cls.bot_user = 'factory-auto'

    wf.create_user(request.cls.bot_user)
    # When creating a review, set the by_user to bot_user
    project.add_reviewers(users=[request.cls.bot_user])

    request.cls.wf = wf

    request.cls.review_bot = CheckerBugowner(request.cls.wf.apiurl, user=request.cls.bot_user, logger=logging.getLogger())

    yield "workflow"
    del request.cls.wf


class TestCheckBugowner(OBSLocal.TestCase):

    @pytest.mark.usefixtures("default_config")
    def test_no_bugowner(self):
        """Declines the request for a new package"""
        req_id = self.wf.create_submit_request('devel:wine', 'merlot').reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn('merlot appears to be a new package', review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_existing_package(self):
        """Accepts requests for existing packages"""
        self.wf.create_package(PROJECT, 'merlot')

        req_id = self.wf.create_submit_request('devel:wine', 'merlot').reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertEqual('ok', review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_invalid_bugowner(self):
        """Declines the request for a new package because of wrong maintainer"""
        req_id = self.wf.create_submit_request('devel:wine', 'merlot', description="bugowner: thatguythere").reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn('thatguythere could not be found on this instance.', review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_valid_bugowner(self):
        """Accept request with valid maintainer"""
        self.wf.create_user('thegirl')
        req_id = self.wf.create_submit_request('devel:wine', 'merlot', description="bugowner: thegirl").reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))

    @pytest.mark.usefixtures("default_config")
    def test_valid_bugowner_with_space(self):
        """Accept request with valid maintainer with space"""
        self.wf.create_user('thegirl')
        req_id = self.wf.create_submit_request('devel:wine', 'merlot', description="bugowner: thegirl ").reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))

    @pytest.mark.usefixtures("default_config")
    def test_valid_bugowner_group(self):
        """Accept request with valid group maintainer"""
        self.wf.create_group('coldpool')
        req_id = self.wf.create_submit_request(
            'devel:wine', 'merlot', description="This is a cool new package\nbugowner: group:coldpool").reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids_search_review()
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
