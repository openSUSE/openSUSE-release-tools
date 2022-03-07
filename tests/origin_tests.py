from datetime import datetime
from osc.core import change_review_state
from osc.core import copy_pac as copy_package
from osc.core import get_request
from osclib.comments import CommentAPI
from osclib.core import attribute_value_delete
from osclib.core import attribute_value_save
from osclib.core import devel_project_get
from osclib.core import request_create_change_devel
from osclib.core import request_state_change
from osclib.memoize import memoize_session_reset
from osclib.origin import config_load
from osclib.origin import config_origin_generator
from osclib.origin import config_origin_list
from osclib.origin import NAME
from osclib.origin import origin_annotation_load
from osclib.origin import origin_find
from osclib.origin import origin_update
import time
import yaml
from . import OBSLocal

# CI-Node: Long2


class TestOrigin(OBSLocal.TestCase):
    script = './origin-manager.py'
    script_debug_osc = False

    def setUp(self):
        super().setUp()

        self.target_project = self.randomString('target')
        self.wf = OBSLocal.FactoryWorkflow(self.target_project)

        self.wf.create_attribute_type('OSRT', 'OriginConfig', 1)

        self.bot_user = self.randomString('bot')
        self.wf.create_user(self.bot_user)

        self.review_user = self.randomString('reviewer')
        self.wf.create_user(self.review_user)

        self.review_group = self.randomString('group')
        self.wf.create_group(self.review_group, [self.review_user])

        target = self.wf.create_project(self.target_project)
        target.update_meta(reviewer={'users': [self.bot_user]})

    def tearDown(self):
        super().tearDown()
        del self.wf

    def remote_config_set_age_minimum(self, minimum=0):
        self.wf.remote_config_set({'originmanager-request-age-min': minimum})

    def origin_config_write(self, origins, extra={}):
        config = {
            'origins': origins,
            'review-user': self.bot_user,
            'fallback-group': self.review_group,
        }
        config.update(extra)
        config = yaml.dump(config, default_flow_style=False)
        attribute_value_save(self.wf.apiurl, self.target_project, 'OriginConfig', config)

    def assertComment(self, request_id, comment):
        comments_actual = CommentAPI(self.wf.api.apiurl).get_comments(request_id=request_id)
        comment_actual = next(iter(comments_actual.values()))
        self.assertEqual(comment_actual['who'], self.bot_user)
        self.assertEqual(comment_actual['comment'], '\n\n'.join(comment))

    def assertAnnotation(self, request_id, annotation):
        request = get_request(self.wf.apiurl, request_id)
        annotation_actual = origin_annotation_load(request, request.actions[0], self.bot_user)

        self.assertTrue(type(annotation_actual) is dict)
        self.assertEqual(annotation_actual, annotation)

    def _assertUpdate(self, package, desired):
        memoize_session_reset()
        self.osc_user(self.bot_user)
        request_future = origin_update(self.wf.apiurl, self.wf.project, package)
        if desired:
            self.assertNotEqual(request_future, False)
            request_id = request_future.print_and_create()
        else:
            self.assertEqual(request_future, False)
            request_id = None
        self.osc_user_pop()

        return request_id

    def assertUpdate(self, package):
        return self._assertUpdate(package, True)

    def assertNoUpdate(self, package):
        return self._assertUpdate(package, False)

    def accept_fallback_review(self, request_id):
        self.osc_user(self.review_user)
        change_review_state(apiurl=self.wf.apiurl,
                            reqid=request_id, newstate='accepted',
                            by_group=self.review_group, message='approved')
        self.osc_user_pop()

    def waitDelta(self, start, delay):
        delta = (datetime.now() - start).total_seconds()
        sleep = max(delay - delta, 0) + 1
        print('sleep', sleep)
        time.sleep(sleep)

    def testRequestMinAge(self):
        self.origin_config_write([])

        request = self.wf.create_submit_request(self.randomString('devel'), self.randomString('package'))
        self.assertReviewScript(request.reqid, self.bot_user, 'new', 'new')
        self.assertOutput(f'skipping {request.reqid} of age')
        self.assertOutput('since it is younger than 1800s')

    def test_config(self):
        attribute_value_save(self.wf.apiurl, self.target_project, 'OriginConfig', 'origins: []')
        config = config_load(self.wf.apiurl, self.wf.project)
        self.assertEqual(config['unknown_origin_wait'], False)
        self.assertEqual(config['review-user'], NAME)

        memoize_session_reset()
        self.origin_config_write([{'fakeProject': {}}, {'*~': {}}])
        config = config_load(self.wf.apiurl, self.wf.project)
        self.assertEqual(config_origin_list(config), ['fakeProject', 'fakeProject~'])
        for _, values in config_origin_generator(config['origins']):
            self.assertEqual(values['automatic_updates'], True)

    def test_no_config(self):
        request = self.wf.create_submit_request(self.randomString('devel'), self.randomString('package'))
        self.assertReviewScript(request.reqid, self.bot_user, 'new', 'accepted', 'skipping since no OSRT:OriginConfig')

    def test_not_allowed_origin(self):
        self.remote_config_set_age_minimum()
        self.origin_config_write([{'fakeProject': {}}], {'unknown_origin_wait': True})

        request = self.wf.create_submit_request(self.randomString('devel'), self.randomString('package'))
        self.assertReviewScript(request.reqid, self.bot_user, 'new', 'new')

        comment = [
            '<!-- OriginManager state=seen result=None -->',
            'Source not found in allowed origins:',
            '- fakeProject',
            f'Decision may be overridden via `@{self.bot_user} override`.',
        ]
        self.assertComment(request.reqid, comment)

        self.origin_config_write([{'fakeProject': {}}], {'unknown_origin_wait': False})
        self.assertReviewScript(request.reqid, self.bot_user, 'new', 'declined', 'review failed')
        comment.pop()
        self.assertComment(request.reqid, comment)

    def test_devel_only(self):
        self.origin_config_write([{'<devel>': {}}])
        self.devel_workflow(True)

    def test_devel_possible(self):
        self.product_project = self.randomString('product')
        self.origin_config_write([
            {'<devel>': {}},
            {self.product_project: {}},
        ], {'unknown_origin_wait': True})
        self.devel_workflow(False)

    def devel_workflow(self, only_devel):
        self.remote_config_set_age_minimum()

        devel_project = self.randomString('devel')
        package = self.randomString('package')
        request = self.wf.create_submit_request(devel_project, package)
        attribute_value_save(self.wf.apiurl, devel_project, 'ApprovedRequestSource', '', 'OBS')

        if not only_devel:
            self.assertReviewScript(request.reqid, self.bot_user, 'new', 'new')

            comment = [
                '<!-- OriginManager state=seen result=None -->',
                'Source not found in allowed origins:',
                f'- {self.product_project}',
                f'Decision may be overridden via `@{self.bot_user} override`.',
            ]
            self.assertComment(request.reqid, comment)

            CommentAPI(self.wf.api.apiurl).add_comment(
                request_id=request.reqid, comment=f'@{self.bot_user} change_devel')

            comment = 'change_devel command by {}'.format('Admin')
        else:
            comment = 'only devel origin allowed'

        self.assertReviewScript(request.reqid, self.bot_user, 'new', 'accepted')
        self.assertAnnotation(request.reqid, {
            'comment': comment,
            'origin': devel_project,
        })

        request.change_state('accepted')

        memoize_session_reset()
        self.osc_user(self.bot_user)
        request_future = origin_update(self.wf.apiurl, self.wf.project, package)
        self.assertNotEqual(request_future, False)
        if request_future:
            request_id_change_devel = request_future.print_and_create()

        # Ensure a second request is not triggered.
        request_future = origin_update(self.wf.apiurl, self.wf.project, package)
        self.assertEqual(request_future, False)
        self.osc_user_pop()

        memoize_session_reset()
        origin_info = origin_find(self.wf.apiurl, self.wf.project, package)
        self.assertEqual(origin_info, None)

        self.assertReviewScript(request_id_change_devel, self.bot_user, 'new', 'accepted')
        self.assertAnnotation(request_id_change_devel, {
            'origin': devel_project,
        })

        # Origin should change before request is accepted since it is properly
        # annotated and without fallback review.
        memoize_session_reset()
        origin_info = origin_find(self.wf.apiurl, self.wf.project, package)
        self.assertEqual(str(origin_info), devel_project)

        self.wf.projects[devel_project].packages[0].create_commit()

        self.osc_user(self.bot_user)
        request_future = origin_update(self.wf.apiurl, self.wf.project, package)
        self.assertNotEqual(request_future, False)
        if request_future:
            request_id_update = request_future.print_and_create()

        request_future = origin_update(self.wf.apiurl, self.wf.project, package)
        self.assertEqual(request_future, False)
        self.osc_user_pop()

        self.assertReviewScript(request_id_update, self.bot_user, 'new', 'accepted')
        self.assertAnnotation(request_id_update, {
            'origin': devel_project,
        })

        memoize_session_reset()
        devel_project_actual, _ = devel_project_get(self.wf.apiurl, self.wf.project, package)
        self.assertEqual(devel_project_actual, None)

        request = get_request(self.wf.apiurl, request_id_change_devel)
        request_state_change(self.wf.apiurl, request_id_change_devel, 'accepted')

        memoize_session_reset()
        devel_project_actual, devel_package_actual = devel_project_get(
            self.wf.apiurl, self.wf.project, package)
        self.assertEqual(devel_project_actual, devel_project)
        self.assertEqual(devel_package_actual, package)

        request = get_request(self.wf.apiurl, request_id_update)
        request_state_change(self.wf.apiurl, request_id_update, 'accepted')

        devel_project_new = self.randomString('develnew')
        self.wf.create_package(devel_project_new, package)
        attribute_value_save(self.wf.apiurl, devel_project_new, 'ApprovedRequestSource', '', 'OBS')

        copy_package(self.wf.apiurl, devel_project, package,
                     self.wf.apiurl, devel_project_new, package)

        request_future = request_create_change_devel(
            self.wf.apiurl, devel_project_new, package, self.wf.project)
        self.assertNotEqual(request_future, False)
        if request_future:
            request_id_change_devel_new = request_future.print_and_create()

        self.assertReviewScript(request_id_change_devel_new, self.bot_user, 'new', 'accepted')
        self.assertAnnotation(request_id_change_devel_new, {
            'origin': devel_project_new,
            'origin_old': devel_project,
        })

        self.accept_fallback_review(request_id_change_devel_new)
        request_state_change(self.wf.apiurl, request_id_change_devel_new, 'accepted')

        memoize_session_reset()
        origin_info = origin_find(self.wf.apiurl, self.wf.project, package)
        self.assertEqual(str(origin_info), devel_project_new)

    def test_split_product(self):
        self.remote_config_set_age_minimum()

        upstream1_project = self.randomString('upstream1')
        upstream2_project = self.randomString('upstream2')
        devel_project = self.randomString('devel')
        package = self.randomString('package')

        self.wf.create_package(self.target_project, package)
        upstream1_package = self.wf.create_package(upstream1_project, package)
        upstream2_package = self.wf.create_package(upstream2_project, package)
        devel_package = self.wf.create_package(devel_project, package)

        upstream1_package.create_commit()
        upstream2_package.create_commit()
        devel_package.create_commit()

        attribute_value_save(self.wf.apiurl, upstream1_project, 'ApprovedRequestSource', '', 'OBS')
        attribute_value_save(self.wf.apiurl, upstream2_project, 'ApprovedRequestSource', '', 'OBS')
        attribute_value_save(self.wf.apiurl, devel_project, 'ApprovedRequestSource', '', 'OBS')

        self.origin_config_write([
            {'<devel>': {}},
            {upstream1_project: {}},
            {upstream2_project: {'pending_submission_consider': True}},
            {'*~': {}},
        ], {'unknown_origin_wait': True})

        # Simulate branch project from upstream1.
        copy_package(self.wf.apiurl, upstream1_project, package,
                     self.wf.apiurl, self.target_project, package)

        memoize_session_reset()
        origin_info = origin_find(self.wf.apiurl, self.target_project, package)
        self.assertEqual(str(origin_info), upstream1_project)

        # Create request against upstream2 which considers pending submissions.
        request_upstream2 = self.wf.submit_package(devel_package, upstream2_project)
        request_target = self.wf.submit_package(devel_package, self.target_project)

        self.assertReviewScript(request_target.reqid, self.bot_user, 'new', 'new')
        comment = [
            '<!-- OriginManager state=seen result=None -->',
            f'Waiting on acceptance of request#{request_upstream2.reqid}.',
        ]
        self.assertComment(request_target.reqid, comment)

        request_upstream2.change_state('accepted')

        self.assertReviewScript(request_target.reqid, self.bot_user, 'new', 'accepted')
        self.assertAnnotation(request_target.reqid, {
            'origin': upstream2_project,
            'origin_old': upstream1_project,
        })

        # Accept fallback review for changing to lower priority origin.
        self.accept_fallback_review(request_target.reqid)
        request_target.change_state('accepted')

        memoize_session_reset()
        origin_info = origin_find(self.wf.apiurl, self.target_project, package)
        self.assertEqual(str(origin_info), upstream2_project)

        # Simulate upstream1 incorporating upstream2 version of package.
        copy_package(self.wf.apiurl, upstream2_project, package,
                     self.wf.apiurl, upstream1_project, package)

        memoize_session_reset()
        origin_info = origin_find(self.wf.apiurl, self.target_project, package)
        self.assertEqual(str(origin_info), upstream1_project)

    def test_new_package_submission(self):
        self.remote_config_set_age_minimum()

        upstream1_project = self.randomString('upstream1')
        upstream2_project = self.randomString('upstream2')
        upstream3_project = self.randomString('upstream3')
        package1 = self.randomString('package1')
        package2 = self.randomString('package2')
        package3 = self.randomString('package3')

        self.wf.create_package(self.target_project, package1)
        upstream1_package1 = self.wf.create_package(upstream1_project, package1)
        self.wf.create_package(upstream2_project, package1)

        upstream1_package1.create_commit()
        copy_package(self.wf.apiurl, upstream1_project, package1,
                     self.wf.apiurl, upstream2_project, package1)

        upstream3_package2 = self.wf.create_package(upstream3_project, package2)
        upstream3_package2.create_commit()

        upstream1_package3 = self.wf.create_package(upstream1_project, package3)
        upstream1_package3.create_commit()

        attribute_value_save(self.wf.apiurl, upstream1_project, 'ApprovedRequestSource', '', 'OBS')
        attribute_value_save(self.wf.apiurl, upstream2_project, 'ApprovedRequestSource', '', 'OBS')
        attribute_value_save(self.wf.apiurl, upstream3_project, 'ApprovedRequestSource', '', 'OBS')

        self.origin_config_write([
            {upstream1_project: {'automatic_updates_initial': True}},
            {upstream2_project: {'automatic_updates_initial': True}},
            {upstream3_project: {}},
        ])

        self.osc_user(self.bot_user)
        memoize_session_reset()
        request_future = origin_update(self.wf.apiurl, self.wf.project, package1)
        self.assertNotEqual(request_future, False)
        if request_future:
            request_future.print_and_create()

        # Ensure a second request is not triggered.
        memoize_session_reset()
        request_future = origin_update(self.wf.apiurl, self.wf.project, package1)
        self.assertEqual(request_future, False)

        # No new package submission from upstream3 since not automatic_updates_initial.
        memoize_session_reset()
        request_future = origin_update(self.wf.apiurl, self.wf.project, package2)
        self.assertEqual(request_future, False)
        self.osc_user_pop()

        upstream2_package2 = self.wf.create_package(upstream2_project, package2)
        upstream2_package2.create_commit()

        self.osc_user(self.bot_user)
        memoize_session_reset()
        request_future = origin_update(self.wf.apiurl, self.wf.project, package2)
        self.assertNotEqual(request_future, False)
        if request_future:
            request_id_package2 = request_future.print_and_create()
        self.osc_user_pop()

        request_state_change(self.wf.apiurl, request_id_package2, 'declined')
        upstream2_package2.create_commit()

        self.osc_user(self.bot_user)
        # No new package submission from upstream2 for new revision since
        # declined initial package submission.
        memoize_session_reset()
        request_future = origin_update(self.wf.apiurl, self.wf.project, package2)
        self.assertEqual(request_future, False)
        self.osc_user_pop()

        # Ensure blacklist prevents initial package submission.
        self.wf.create_attribute_type('OSRT', 'OriginUpdateInitialBlacklist', 1)
        attribute_value_save(self.wf.apiurl, self.target_project, 'OriginUpdateInitialBlacklist', package3)
        self.assertNoUpdate(package3)

        attribute_value_delete(self.wf.apiurl, self.target_project, 'OriginUpdateInitialBlacklist')
        self.assertUpdate(package3)

    def test_automatic_update_modes(self):
        self.remote_config_set_age_minimum()

        upstream1_project = self.randomString('upstream1')
        package1 = self.randomString('package1')

        self.wf.create_package(self.target_project, package1)
        upstream1_package1 = self.wf.create_package(upstream1_project, package1)

        upstream1_package1.create_commit()
        copy_package(self.wf.apiurl, upstream1_project, package1,
                     self.wf.apiurl, self.target_project, package1)

        attribute_value_save(self.wf.apiurl, upstream1_project, 'ApprovedRequestSource', '', 'OBS')
        self.wf.create_attribute_type('OSRT', 'OriginUpdateSkip', 0)

        def config_write(delay=0, supersede=True, frequency=0):
            self.origin_config_write([
                {upstream1_project: {
                    'automatic_updates_delay': delay,
                    'automatic_updates_supersede': supersede,
                    'automatic_updates_frequency': frequency,
                }},
            ])

        # Default config with fresh commit.
        config_write()
        upstream1_package1.create_commit()

        # Check the full order of precidence available to mode attributes.
        for project in (upstream1_project, self.target_project):
            for package in (package1, None):
                # Ensure no update is triggered due to OSRT:OriginUpdateSkip.
                attribute_value_save(self.wf.apiurl, project, 'OriginUpdateSkip', '', package=package)
                self.assertNoUpdate(package1)
                attribute_value_delete(self.wf.apiurl, project, 'OriginUpdateSkip', package=package)

        # Configure a delay, make commit, and ensure no update until delayed.
        delay = 17  # Allow enough time for API speed fluctuation.
        config_write(delay=delay)
        upstream1_package1.create_commit()
        start = datetime.now()

        self.assertNoUpdate(package1)
        self.waitDelta(start, delay)
        request_id_package1_1 = self.assertUpdate(package1)

        # Configure no supersede and ensure no update generated for new commit.
        config_write(supersede=False)
        upstream1_package1.create_commit()
        self.assertNoUpdate(package1)

        # Accept request and ensure update since no request to supersede.
        self.assertReviewScript(request_id_package1_1, self.bot_user, 'new', 'accepted')
        request_state_change(self.wf.apiurl, request_id_package1_1, 'accepted')
        self.assertUpdate(package1)

        # Track time since last request created for testing frequency.
        start = datetime.now()

        # Configure frequency (removes supersede=False).
        config_write(frequency=delay)

        upstream1_package1.create_commit()
        self.assertNoUpdate(package1)

        # Fresh commit should not impact frequency which only looks at requests.
        self.waitDelta(start, delay)
        upstream1_package1.create_commit()

        self.assertUpdate(package1)
