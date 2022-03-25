import logging
from . import OBSLocal
from check_source import CheckSource
import os
from osc.core import get_request_list
import pytest

PROJECT = 'Testing:Project'
SRC_PROJECT = 'devel:Fishing'
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
REVIEW_TEAM = 'reviewers-team'
FACTORY_MAINTAINERS = 'group:factory-maintainers'

# NOTE: Since there is no documentation explaining the good practices for creating tests for a
# review bot, this test is created by mimicking parts of other existing tests. Most decisions are
# documented, but some rationales may be wrong. So don't take this as a model for creating
# future tests.

# Inherit from OBSLocal.Testcase since it provides many commodity methods for testing against
# a local testing instance of OBS
# CI-Node: Long1


def _common_workflow(request):
    # Using OBSLocal.FactoryWorkflow makes it easier to setup testing scenarios
    wf = OBSLocal.FactoryWorkflow(PROJECT)
    project = wf.projects[PROJECT]

    # Set up the reviewers team
    wf.create_group(REVIEW_TEAM)

    request.cls.bot_user = 'factory-auto'

    wf.create_user(request.cls.bot_user)
    # When creating a review, set the by_user to bot_user
    project.add_reviewers(users=[request.cls.bot_user])

    request.cls.wf = wf


def _add_review_bot(request):
    # StagingWorkflow creates reviews with the reviewer set to bot_user, so it's necessary to
    # configure our review bot to act upon such reviews
    request.cls.review_bot = CheckSource(request.cls.wf.apiurl, user=request.cls.bot_user, logger=logging.getLogger())


@pytest.fixture
def required_source_maintainer(request):
    _common_workflow(request)
    request.cls.wf.remote_config_set(
        {'required-source-maintainer': 'Admin', 'review-team': REVIEW_TEAM, 'devel-project-enforce': 'True'}
    )
    _add_review_bot(request)
    yield "workflow"
    del request.cls.wf


@pytest.fixture
def default_config(request):
    _common_workflow(request)
    request.cls.wf.remote_config_set(
        {'review-team': REVIEW_TEAM, 'devel-project-enforce': 'True'}
    )
    _add_review_bot(request)
    yield "workflow"
    del request.cls.wf


class TestCheckSource(OBSLocal.TestCase):

    @pytest.mark.usefixtures("default_config")
    def test_no_devel_project(self):
        """Declines the request when it does not come from a devel project"""
        req_id = self.wf.create_submit_request(SRC_PROJECT, self.randomString('package')).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn('%s is not a devel project of %s' % (SRC_PROJECT, PROJECT), review.comment)

    @pytest.mark.usefixtures("required_source_maintainer")
    def test_devel_project(self):
        """Accepts a request coming from a devel project"""
        self._setup_devel_project()

        req_id = self.wf.create_submit_request(SRC_PROJECT, 'blowfish', add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertReview(req_id, by_group=(REVIEW_TEAM, 'new'))

    @pytest.mark.usefixtures("default_config")
    def test_missing_patch_in_changelog(self):
        """Reject a request if it adds patch and it is not mentioned in changelog"""
        # devel files contain patch but not changes
        self._setup_devel_project(devel_files='blowfish-with-patch')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn(
            'A patch (test.patch) is being added without this addition being mentioned in the changelog.',
            review.comment
        )

    @pytest.mark.usefixtures("default_config")
    def test_patch_in_changelog(self):
        """Accepts a request if it adds patch and it is mentioned in changelog"""
        self._setup_devel_project()

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertReview(req_id, by_group=(REVIEW_TEAM, 'new'))

    @pytest.mark.usefixtures("default_config")
    def test_revert_of_patch(self):
        """Accepts a request if it reverts addition of patch"""
        # switch target and devel, so basically do revert of changes done
        # with patch and changes
        self._setup_devel_project(devel_files='blowfish',
                                  target_files='blowfish-with-patch-changes')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertReview(req_id, by_group=(REVIEW_TEAM, 'new'))

    @pytest.mark.usefixtures("default_config")
    def test_patch_as_source(self):
        """Accepts a request if a new patch is a source"""
        # switch target and devel, so basically do revert of changes done
        # with patch and changes
        self._setup_devel_project(devel_files='blowfish-patch-as-source',
                                  target_files='blowfish')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertReview(req_id, by_group=(REVIEW_TEAM, 'new'))

    @pytest.mark.usefixtures("required_source_maintainer")
    def test_no_source_maintainer(self):
        """Declines the request when the 'required_maintainer' is not maintainer of the source project

          Create also request to add required maintainers to source project unless it is already open
        """
        self._setup_devel_project()

        # Change the required maintainer
        self.wf.create_group(FACTORY_MAINTAINERS.replace('group:', ''))
        self.wf.remote_config_set({'required-source-maintainer': FACTORY_MAINTAINERS})

        req = self.wf.create_submit_request(SRC_PROJECT, 'blowfish', add_commit=False)

        self.assertReview(req.reqid, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req.reqid])
        self.review_bot.check_requests()

        review = self.assertReview(req.reqid, by_user=(self.bot_user, 'declined'))
        add_role_req = get_request_list(self.wf.apiurl, SRC_PROJECT, req_state=['new'], req_type='add_role')[0]

        self.assertIn('unless %s is a maintainer of %s' % (FACTORY_MAINTAINERS, SRC_PROJECT), review.comment)
        self.assertIn('Created the add_role request %s' % add_role_req.reqid, review.comment)

        self.assertEqual(add_role_req.actions[0].tgt_project, SRC_PROJECT)
        self.assertEqual('Created automatically from request %s' % req.reqid, add_role_req.description)

        # reopen request and do it again to test that new add_role request won't be created
        req.change_state('new')

        self.review_bot.check_requests()
        add_role_reqs = get_request_list(self.wf.apiurl, SRC_PROJECT, req_state=['new'], req_type='add_role')

        self.assertEqual(len(add_role_reqs), 1)

    @pytest.mark.usefixtures("required_source_maintainer")
    def test_source_maintainer(self):
        """Accepts the request when the 'required_maintainer' is a group and is a maintainer for the project"""
        group_name = FACTORY_MAINTAINERS.replace('group:', '')
        self.wf.create_group(group_name)
        self.wf.remote_config_set({'required-source-maintainer': FACTORY_MAINTAINERS})

        self._setup_devel_project(maintainer={'groups': [group_name]})

        req_id = self.wf.create_submit_request(SRC_PROJECT, 'blowfish', add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))
        self.assertReview(req_id, by_group=(REVIEW_TEAM, 'new'))

    @pytest.mark.usefixtures("required_source_maintainer")
    def test_source_inherited_maintainer(self):
        """Declines the request when the 'required_maintainer' is only inherited maintainer of the source project"""
        # Change the required maintainer
        group_name = FACTORY_MAINTAINERS.replace('group:', '')
        self.wf.create_group(group_name)
        self.wf.remote_config_set({'required-source-maintainer': FACTORY_MAINTAINERS})

        self.wf.create_project(SRC_PROJECT.rsplit(':', 1)[0], maintainer={'groups': [group_name]})

        self._setup_devel_project()

        req = self.wf.create_submit_request(SRC_PROJECT, 'blowfish', add_commit=False)

        self.assertReview(req.reqid, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req.reqid])
        self.review_bot.check_requests()

        review = self.assertReview(req.reqid, by_user=(self.bot_user, 'declined'))
        add_role_req = get_request_list(self.wf.apiurl, SRC_PROJECT, req_state=['new'], req_type='add_role')[0]

        self.assertIn('unless %s is a maintainer of %s' % (FACTORY_MAINTAINERS, SRC_PROJECT), review.comment)
        self.assertIn('Created the add_role request %s' % add_role_req.reqid, review.comment)

        self.assertEqual(add_role_req.actions[0].tgt_project, SRC_PROJECT)
        self.assertEqual('Created automatically from request %s' % req.reqid, add_role_req.description)

    @pytest.mark.usefixtures("default_config")
    def test_bad_rpmlintrc(self):
        """Declines a request if it uses setBadness in rpmlintrc"""
        self._setup_devel_project(devel_files='blowfish-with-rpmlintrc')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertEqual('For product submissions, you cannot use setBadness. Use filters in blowfish/blowfish-rpmlintrc.', review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_remote_service(self):
        """Declines _service files with remote services"""
        self._setup_devel_project(devel_files='blowfish-with-remote-service')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertEqual('Services are only allowed if their mode is one of localonly, disabled, buildtime, ' +
                         'manual. Please change the mode of recompress and use `osc service localrun/disabledrun`.', review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_wrong_name(self):
        """Declines spec files with wrong name"""
        self._setup_devel_project(devel_files='blowfish-with-broken-name')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertEqual("A package submitted as blowfish has to build as 'Name: blowfish' - found Name 'suckfish'", review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_without_copyright(self):
        """Declines spec files without copyright"""
        self._setup_devel_project(devel_files='blowfish-without-copyright')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn("blowfish.spec does not appear to contain a Copyright comment.", review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_no_changelog(self):
        """Declines submit request with just changed spec file"""
        self._setup_devel_project(devel_files='blowfish-without-changes-update')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn("No changelog. Please use 'osc vc' to update the changes file(s).", review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_no_license(self):
        """Declines spec files without a (minimal) license"""
        self._setup_devel_project(devel_files='blowfish-without-license')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertIn("blowfish.spec does not appear to have a license. The file needs to contain a free software license", review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_not_mentioned(self):
        """Declines untracked files"""
        self._setup_devel_project(devel_files='blowfish-not-mentioned')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        review = self.assertReview(req_id, by_user=(self.bot_user, 'declined'))
        self.assertEqual("Attention, README is not mentioned in spec files as source or patch.", review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_source_urls(self):
        """Soft-Declines invalid source URLs"""
        self._setup_devel_project(devel_files='blowfish-with-urls')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        # not declined but not accepted either
        review = self.assertReview(req_id, by_user=(self.bot_user, 'new'))
        self.assertIn("Source URLs are not valid. Try `osc service runall download_files`.\nblowfish-1.tar.gz", review.comment)

    @pytest.mark.usefixtures("default_config")
    def test_existing_source_urls(self):
        """Accepts invalid source URLs if previously present"""
        self._setup_devel_project(devel_files='blowfish-with-urls', target_files='blowfish-with-existing-url')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))

    @pytest.mark.usefixtures("default_config")
    def test_two_patches_in_one_line(self):
        """Accepts patches even if mentioned in one line"""
        self._setup_devel_project(devel_files='blowfish-with-two-patches')

        req_id = self.wf.create_submit_request(self.devel_package.project,
                                               self.devel_package.name, add_commit=False).reqid

        self.assertReview(req_id, by_user=(self.bot_user, 'new'))

        self.review_bot.set_request_ids([req_id])
        self.review_bot.check_requests()

        self.assertReview(req_id, by_user=(self.bot_user, 'accepted'))

    def _setup_devel_project(self, maintainer={}, devel_files='blowfish-with-patch-changes',
                             target_files='blowfish'):
        devel_project = self.wf.create_project(SRC_PROJECT, maintainer=maintainer)
        self.devel_package = OBSLocal.Package('blowfish', project=devel_project)

        fixtures_path = os.path.join(FIXTURES, 'packages', devel_files)
        self.devel_package.commit_files(fixtures_path)
        self.devel_package.wait_services()

        fixtures_path = os.path.join(FIXTURES, 'packages', target_files)
        self.target_package = OBSLocal.Package('blowfish', self.wf.projects[PROJECT], devel_project=SRC_PROJECT)
        self.target_package.commit_files(fixtures_path)
