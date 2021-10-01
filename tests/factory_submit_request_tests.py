from . import OBSLocal
import random
import os

# Needed to mock LegalAuto
from osc.core import change_review_state
from mock import MagicMock

# Import the involved staging commands
from osclib.freeze_command import FreezeCommand
from osclib.select_command import SelectCommand
from osclib.accept_command import AcceptCommand

# Import the involved bots
from check_source import CheckSource
legal_auto = __import__("legal-auto") # Needed because of the dash in the filename
LegalAuto = legal_auto.LegalAuto

PROJECT = 'openSUSE:Factory'
DEVEL_PROJECT = 'devel:drinking'
STAGING_PROJECT_NAME = 'openSUSE:Factory:Staging:A'
HUMAN_REVIEWER = 'factory-cop'
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

class TestFactorySubmitRequest(OBSLocal.TestCase):
    """Tests for the whole lifecycle of submit requests in Factory

    This test is intended to showcase the typical workflow of new submit request for a ring package
    in Factory. Good for newcommers and to serve as a reference to create tests for similar
    scenarios.

    The goal is not to test all possible combinations of things that could go wrong with every
    review bot. Please use separate per-bot tests (like check_source_test.py) for that.

    This is also useful as smoke test, to check that all the pieces keep working together.
    """

    def setUp(self):
        super(TestFactorySubmitRequest, self).setUp()

        # Setup the basic scenario, with manual reviewers, staging projects, rings and wine as
        # example package (wine is in ring1, see OBSLocal.FactoryWorkflow.setup_rings)
        self.wf = OBSLocal.FactoryWorkflow(PROJECT)
        self.__setup_review_team()
        self.__setup_devel_package('wine')
        self.wf.setup_rings(devel_project=DEVEL_PROJECT)

        # Relax the requisites to send a submit request to Factory,
        # so the CheckSource bot is easier to please
        self.wf.remote_config_set({'required-source-maintainer': ''})

        # Setup the different bots typically used for Factory
        self.setup_review_bot(self.wf, PROJECT, 'factory-auto', CheckSource)
        self.setup_review_bot(self.wf, PROJECT, 'licensedigger', LegalAuto)

        # Sorry, but LegalAuto is simply too hard to test while keeping this test readable,
        # see the description of __mock_licendigger for more rationale
        self.__mock_licensedigger()

        # The staging project must be frozen in order to move packages into it
        FreezeCommand(self.wf.api).perform(STAGING_PROJECT_NAME)

        # Create the submit request
        self.request = self.wf.create_submit_request(DEVEL_PROJECT, 'wine', add_commit=False)

    def tearDown(self):
        super().tearDown()
        del self.wf

    def test_happy_path(self):
        """Tests the ideal case in which all bots are happy and the request successfully goes
        through staging"""
        # Initial state: reviews have been created for the bots and for the staging workflow
        reqid = self.request.reqid
        self.assertReview(reqid, by_user=('factory-auto', 'new'))
        self.assertReview(reqid, by_user=('licensedigger', 'new'))
        self.assertReview(reqid, by_group=('factory-staging', 'new'))

        # Let bots come into play
        self.execute_review_bot([reqid], 'factory-auto')
        self.execute_review_bot([reqid], 'licensedigger')

        # Bots are happy, now it's time for manual review (requested by the bots) and
        # for the staging work
        self.assertReview(reqid, by_user=('factory-auto', 'accepted'))
        self.assertReview(reqid, by_user=('licensedigger', 'accepted'))

        # This review will be accepted when the Staging Manager puts it into a staging project
        self.assertReview(reqid, by_group=('factory-staging', 'new'))

        # Review created by CheckSource bot. This review should be manually accepted.
        self.assertReview(reqid, by_group=('opensuse-review-team', 'new'))

        # Let's first accept the manual review
        change_review_state(
            apiurl = self.wf.apiurl, reqid = reqid,
            newstate = 'accepted', by_group='opensuse-review-team'
        )

        # Now only the staging workflow is pending
        self.assertReview(reqid, by_user=('factory-auto', 'accepted'))
        self.assertReview(reqid, by_user=('licensedigger', 'accepted'))
        self.assertReview(reqid, by_group=('opensuse-review-team', 'accepted'))
        self.assertReview(reqid, by_group=('factory-staging', 'new'))

        # Before using the staging plugin, we need to force a reload of the configuration
        # because execute_review_bot temporarily switches the user and that causes problems
        self.wf.load_config()

        # The Staging Manager puts the request into a staging project
        SelectCommand(self.wf.api, STAGING_PROJECT_NAME).perform(['wine'])

        # The factory-staging review is now accepted and a new review associated to the
        # staging project has been created
        self.assertReview(reqid, by_group=('factory-staging', 'accepted'))
        self.assertReview(reqid, by_project=(STAGING_PROJECT_NAME, 'new'))

        # Let's say everything looks good in the staging project and the Staging Manager accepts it
        AcceptCommand(self.wf.api).accept_all([STAGING_PROJECT_NAME], True)

        # Finally, all the reviews are accepted: one for each bot, one for manual review and
        # two for the staging project (one as a consequence of selecting the package into a
        # staging project and the other as a consequence of accepting the staging)
        self.assertReview(reqid, by_user=('factory-auto', 'accepted'))
        self.assertReview(reqid, by_user=('licensedigger', 'accepted'))
        self.assertReview(reqid, by_group=('opensuse-review-team', 'accepted'))
        self.assertReview(reqid, by_group=('factory-staging', 'accepted'))
        self.assertReview(reqid, by_project=(STAGING_PROJECT_NAME, 'accepted'))

        # So it's time to accept the request
        self.request.change_state('accepted')
        self.assertRequestState(reqid, name='accepted')

    def __setup_devel_package(self, pkg_name):
        pkg = self.wf.create_package(DEVEL_PROJECT, pkg_name)
        pkg.commit_files(os.path.join(FIXTURES, 'packages', pkg_name))

    def __setup_review_team(self):
        """Creates the review team with some user on it

        According to the default configuration for Factory, the CheckSource bot must create a review
        for the group 'opensuse-review-team' for each request that passes the automatic checks.
        That behavior can be configured with the following two parameters: 'review-team' and
        'check-source-add-review-team'. This function ensures the test can work with the default
        configuration, to serve as a realistic example.
        """
        self.wf.create_user(HUMAN_REVIEWER)
        self.wf.create_group('opensuse-review-team', users=[HUMAN_REVIEWER])

    def __mock_licensedigger(self):
        """Mocks the execution of the LegalAuto bot, so it always succeeds and accepts the review

        Setting up a bot and then just mocking its whole execution may look pointless, but this
        testcase was conceived as a showcase of the Factory workflow, so all relevant bots should be
        represented. Unfortunatelly, LegalAuto is not written to be testable and it's very dependant
        on external components. Hopefully this whole mock could be removed in the future.
        """
        bot = self.review_bots['licensedigger']
        bot.check_requests = MagicMock(side_effect=self.__accept_license)

    def __accept_license(self):
        """See :func:`__mock_licensedigger`"""
        change_review_state(
            apiurl = self.wf.apiurl, reqid = self.request.reqid,
            newstate = 'accepted', by_user='licensedigger'
        )
