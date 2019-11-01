from osc.core import change_review_state
from osc.core import copy_pac as copy_package
from osc.core import get_request
from osclib.comments import CommentAPI
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
import yaml
from . import OBSLocal

class TestOrigin(OBSLocal.TestCase):
    script = './origin-manager.py'
    script_debug_osc = False

    def setUp(self):
        super().setUp()

        self.target_project = self.randomString('target')
        self.wf = OBSLocal.StagingWorkflow(self.target_project)

        self.wf.create_attribute_type('OSRT', 'OriginConfig', 1)

        self.bot_user = self.randomString('bot')
        self.wf.create_user(self.bot_user)

        self.review_user = self.randomString('reviewer')
        self.wf.create_user(self.review_user)

        self.review_group = self.randomString('group')
        self.wf.create_group(self.review_group, [self.review_user])

        self.wf.create_project(self.target_project, reviewer={'users': [self.bot_user]})

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

    def accept_fallback_review(self, request_id):
        self.osc_user(self.review_user)
        change_review_state(apiurl=self.wf.apiurl,
                            reqid=request_id, newstate='accepted',
                            by_group=self.review_group, message='approved')
        self.osc_user_pop()

    def testRequestMinAge(self):
        self.origin_config_write([])

        request = self.wf.create_submit_request(self.randomString('devel'), self.randomString('package'))
        self.assertReviewBot(request.reqid, self.bot_user, 'new', 'new')
        self.assertOutput(f'skipping {request.reqid} of age')
        self.assertOutput(f'since it is younger than 1800s')

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
        self.assertReviewBot(request.reqid, self.bot_user, 'new', 'accepted', 'skipping since no OSRT:OriginConfig')

    def test_not_allowed_origin(self):
        self.remote_config_set_age_minimum()
        self.origin_config_write([{'fakeProject': {}}], {'unknown_origin_wait': True})

        request = self.wf.create_submit_request(self.randomString('devel'), self.randomString('package'))
        self.assertReviewBot(request.reqid, self.bot_user, 'new', 'new')

        comment = [
            '<!-- OriginManager state=seen result=None -->',
            'Source not found in allowed origins:',
            '- fakeProject',
        ]
        self.assertComment(request.reqid, comment)

        self.origin_config_write([{'fakeProject': {}}], {'unknown_origin_wait': False})
        self.assertReviewBot(request.reqid, self.bot_user, 'new', 'declined', 'review failed')
        self.assertComment(request.reqid, comment)
