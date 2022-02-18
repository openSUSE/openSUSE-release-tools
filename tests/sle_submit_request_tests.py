import logging
from . import OBSLocal
import random
import os

# Needed to configure OriginManager
import yaml
from osclib.core import attribute_value_save

# Needed to mock LegalAuto
from osc.core import change_review_state
from mock import MagicMock

# Import the involved staging commands
from osclib.freeze_command import FreezeCommand
from osclib.select_command import SelectCommand
from osclib.accept_command import AcceptCommand

# Import the involved bots
from check_source import CheckSource
from check_tags_in_requests import TagChecker
legal_auto = __import__("legal-auto")  # Needed because of the dash in the filename
LegalAuto = legal_auto.LegalAuto
origin_manager = __import__("origin-manager")  # Same than above, dash in the filename
OriginManager = origin_manager.OriginManager

PROJECT = 'SUSE:SLE-15-SP3:GA'
DEVEL_PROJECT = 'devel:drinking'
STAGING_PROJECT_NAME = 'SUSE:SLE-15-SP3:GA:Staging:A'
HUMAN_REVIEWER = 'release-manager'
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')


class TestSLESubmitRequest(OBSLocal.TestCase):
    """Tests for the whole lifecycle of submit requests in SLE

    Similar in purpose and philosophy to TestFactorySubmitRequest, check
    factory_submit_request_tests.py for more details
    """

    def setUp(self):
        super(TestSLESubmitRequest, self).setUp()

        # Setup the basic scenario, with manual reviewers, staging projects[...]
        self.wf = OBSLocal.SLEWorkflow(PROJECT)
        self.__setup_review_teams()
        self.__setup_devel_package('wine')
        self.__config_origin_manager()

        # Setup the different bots typically used for Factory
        self.setup_review_bot(self.wf, PROJECT, 'factory-auto', CheckSource)
        self.setup_review_bot(self.wf, PROJECT, 'licensedigger', LegalAuto)
        self.setup_review_bot(self.wf, PROJECT, 'sle-changelog-checker', TagChecker)
        self.setup_review_bot(self.wf, PROJECT, 'origin-manager', OriginManager)

        # Simulating the environment to please some of the bots while keeping this test readable
        # may be a bit more tricky than it seems. Check the descriptions of __mock_licendigger and
        # __mock_changelog_checker for more rationale
        self.__mock_licensedigger()
        self.__mock_changelog_checker()

        # The staging project must be frozen in order to move packages into it
        FreezeCommand(self.wf.api).perform(STAGING_PROJECT_NAME)

        # Create the submit request
        self.request = self.wf.create_submit_request(DEVEL_PROJECT, 'wine', add_commit=False)

    def tearDown(self):
        super().tearDown()
        del self.wf

    def project(self):
        return self.wf.projects[PROJECT]

    def test_happy_path(self):
        """Tests the ideal case in which all bots are happy and the request successfully goes
        through staging"""

        reqid = self.request.reqid

        # Initial state: reviews have been created for...
        # ...three human reviewers...
        self.assertReview(reqid, by_group=('sle-release-managers', 'new'))
        self.assertReview(reqid, by_group=('autobuild-team', 'new'))
        self.assertReview(reqid, by_group=('origin-reviewers', 'new'))
        # ...for the staging workflow...
        self.assertReview(reqid, by_group=('sle-staging-managers', 'new'))

        # ...and for the bots.
        # So let's first execute the bots and verify their results
        self.assertReviewBot(reqid, 'factory-auto', 'new', 'accepted')
        self.assertReviewBot(reqid, 'licensedigger', 'new', 'accepted')
        self.assertReviewBot(reqid, 'origin-manager', 'new', 'accepted')
        self.assertReviewBot(reqid, 'sle-changelog-checker', 'new', 'accepted')

        # So now that bots are happy, let's accept the manual reviews
        self.osc_user(HUMAN_REVIEWER)
        change_review_state(self.wf.apiurl, reqid, 'accepted', by_group='sle-release-managers')
        change_review_state(self.wf.apiurl, reqid, 'accepted', by_group='autobuild-team')
        change_review_state(self.wf.apiurl, reqid, 'accepted', by_group='origin-reviewers')
        self.osc_user_pop()

        # Now only the staging workflow is pending
        self.assertReview(reqid, by_group=('sle-release-managers', 'accepted'))
        self.assertReview(reqid, by_group=('autobuild-team', 'accepted'))
        self.assertReview(reqid, by_group=('origin-reviewers', 'accepted'))
        self.assertReview(reqid, by_group=('sle-staging-managers', 'new'))

        # Before using the staging plugin, we need to force a reload of the configuration
        # because assertReviewBot temporarily switches the user and that causes problems
        self.wf.load_config()

        # One staging manager puts the request into the staging project
        SelectCommand(self.wf.api, STAGING_PROJECT_NAME).perform(['wine'])

        # The sle-staging-managers review is now accepted and a new review associated to
        # the staging project has been created
        self.assertReview(reqid, by_group=('sle-staging-managers', 'accepted'))
        self.assertReview(reqid, by_project=(STAGING_PROJECT_NAME, 'new'))

        # Let's say everything looks good in the staging project, so the staging manager can
        # accept that staging
        AcceptCommand(self.wf.api).accept_all([STAGING_PROJECT_NAME], True)

        # Finally, all the reviews are accepted:
        # ...one for each bot,
        self.assertReview(reqid, by_user=('factory-auto', 'accepted'))
        self.assertReview(reqid, by_user=('licensedigger', 'accepted'))
        self.assertReview(reqid, by_user=('sle-changelog-checker', 'accepted'))
        self.assertReview(reqid, by_user=('origin-manager', 'accepted'))
        # ...one for each manual review
        self.assertReview(reqid, by_group=('sle-release-managers', 'accepted'))
        self.assertReview(reqid, by_group=('autobuild-team', 'accepted'))
        self.assertReview(reqid, by_group=('origin-reviewers', 'accepted'))
        # ...and two for the staging project (one as a consequence of selecting the package into a
        # staging project and the other as a consequence of accepting the staging)
        self.assertReview(reqid, by_group=('sle-staging-managers', 'accepted'))
        self.assertReview(reqid, by_project=(STAGING_PROJECT_NAME, 'accepted'))

        # So it's time to accept the request
        self.request.change_state('accepted')
        self.assertRequestState(reqid, name='accepted')

    def __setup_devel_package(self, pkg_name):
        pkg = self.wf.create_package(DEVEL_PROJECT, pkg_name)
        pkg.commit_files(os.path.join(FIXTURES, 'packages', pkg_name))

        target_pkg = OBSLocal.Package(pkg_name, project=self.project(), devel_project=DEVEL_PROJECT)
        target_pkg.create_commit()

    def __setup_review_teams(self):
        """Creates the different review teams for manual reviews.

        For simplicity, this uses a common user for all groups.

        This also sets those groups as reviewers of the target project, to ensure new reviews are
        created for them as soon as the request is created. Note the difference with the Factory
        workflow, in which the groups of human reviewers are not initially set as reviewers in the
        target project configuration. Instead, in Factory the review targeting the human reviewers
        is created by the factory-auto (CheckSource) bot.
        """
        self.wf.create_user(HUMAN_REVIEWER)
        groups = ['sle-release-managers', 'origin-reviewers', 'autobuild-team']
        for group in groups:
            self.wf.create_group(group, users=[HUMAN_REVIEWER])
            self.project().add_reviewers(groups = [group])

    def __config_origin_manager(self):
        """Creates the very minimal configuration needed by origin-manager to work"""
        self.wf.create_attribute_type('OSRT', 'OriginConfig', 1)
        self.wf.remote_config_set({'originmanager-request-age-min': 0})
        config = {
            'origins': [{'<devel>': {}}],
            'review-user': 'origin-manager',
            'fallback-group': 'origin-reviewers'
        }
        config = yaml.dump(config, default_flow_style=False)
        attribute_value_save(self.wf.apiurl, PROJECT, 'OriginConfig', config)

    def __mock_changelog_checker(self):
        """Mocks the verification done by the TagChecker.

        Normally, the bot checks whether the request references an entry in any of the known
        issue trackers or whether the same request has been sent to Factory. Although simulating one
        of those scenarios looks easy (and probably is), the whole check is mocked to return True
        for simplicity (the rest of the execution of the bot still takes place normally).
        """
        bot = self.review_bots['sle-changelog-checker']
        bot.checkTagInRequest = MagicMock(return_value = True)

    def __mock_licensedigger(self):
        """Mocks the execution of the LegalAuto bot, so it always succeeds and accepts the review

        Unfortunatelly, LegalAuto is not written to be testable and it's very dependant on external
        components. So just mocking its whole execution looks like the simplest solution for the
        time being. Hopefully this whole mock could be removed in the future.
        """
        bot = self.review_bots['licensedigger']
        bot.check_requests = MagicMock(side_effect=self.__accept_license)

    def __accept_license(self):
        """See :func:`__mock_licensedigger`"""
        change_review_state(
            apiurl = self.wf.apiurl, reqid = self.request.reqid,
            newstate = 'accepted', by_user='licensedigger'
        )
