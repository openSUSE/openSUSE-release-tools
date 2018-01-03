import logging
from OBSLocal import OBSLocalTestCase
from osclib.comments import CommentAPI
from ReviewBot import ReviewBot
import random


COMMENT = 'short comment'
PROJECT = 'openSUSE:Factory:Staging'

class TestReviewBotComment(OBSLocalTestCase):
    def setUp(self):
        super(TestReviewBotComment, self).setUp()
        self.api = CommentAPI(self.apiurl)

        # Ensure different test runs operate in unique namespace.
        self.bot = '::'.join([type(self).__name__, str(random.getrandbits(8))])
        self.review_bot = ReviewBot(self.apiurl, logger=logging.getLogger(self.bot))
        self.review_bot.bot_name = self.bot

        self.osc_user('factory-auto')

    def tearDown(self):
        self.api.delete_from(project_name=PROJECT)
        self.assertFalse(len(self.api.get_comments(project_name=PROJECT)))

    def test_basic_logger(self):
        comment_count = len(self.api.get_comments(project_name=PROJECT))
        self.assertFalse(self.comments_filtered(self.bot)[0])

        # Initial comment.
        self.review_bot.comment_handler_add()
        self.review_bot.logger.info('something interesting')
        self.review_bot.comment_write(project=PROJECT)
        comment, _ = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith('something interesting'))

        # Second comment with extra line.
        self.review_bot.comment_handler_add()
        self.review_bot.logger.info('something interesting')
        self.review_bot.logger.info('something extra')
        self.review_bot.comment_write(project=PROJECT)
        comment, _ = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith('something extra'))

    def test_workflow(self):
        comment_count = len(self.api.get_comments(project_name=PROJECT))
        self.assertFalse(self.comments_filtered(self.bot)[0])

        # Initial comment.
        info = {'state': 'seen', 'result': 'failed'}
        info_extra = {'build': '1'}
        info_merged = info.copy()
        info_merged.update(info_extra)
        self.review_bot.comment_write(state='seen', result='failed', identical=True,
                                      info_extra=info_extra, info_extra_identical=False,
                                      project=PROJECT, message=COMMENT)
        comment, info_parsed = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith(COMMENT))
        self.assertEqual(info_parsed, info_merged)

        # Only build change (expect no change).
        info_extra = {'build': '2'}
        self.review_bot.comment_write(state='seen', result='failed', identical=True,
                                      info_extra=info_extra, info_extra_identical=False,
                                      project=PROJECT, message=COMMENT)
        comment, info_parsed = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith(COMMENT))
        self.assertEqual(info_parsed, info_merged)

        # Build and comment (except comment replacement).
        info_extra = {'build': '3'}
        info_merged.update(info_extra)
        self.review_bot.comment_write(state='seen', result='failed', identical=True,
                                      info_extra=info_extra, info_extra_identical=False,
                                      project=PROJECT, message=COMMENT + '3')
        comment, info_parsed = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith(COMMENT + '3'))
        self.assertEqual(info_parsed, info_merged)

        # Final build (except comment replacement).
        info_extra = {'build': '4'}
        info_merged.update(info_extra)
        self.review_bot.comment_write(state='seen', result='failed', identical=True,
                                      info_extra=info_extra, info_extra_identical=True,
                                      project=PROJECT, message=COMMENT + '4')
        comment, info_parsed = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith(COMMENT + '4'))
        self.assertEqual(info_parsed, info_merged)

        # Final build (except comment replacement).
        info = {'state': 'done', 'result': 'passed'}
        info_extra = {'build': '5'}
        info_merged = info.copy()
        info_merged.update(info_extra)
        self.review_bot.comment_write(state='done', result='passed', identical=True,
                                      info_extra=info_extra, info_extra_identical=True,
                                      only_replace=True,
                                      project=PROJECT, message=COMMENT + '5')
        comment, info_parsed = self.comments_filtered(self.bot)
        self.assertTrue(comment['comment'].endswith(COMMENT + '5'))
        self.assertEqual(info_parsed, info_merged)

        # Should never be more than one new comment.
        self.assertEqual(len(self.api.get_comments(project_name=PROJECT)), comment_count + 1)

    def test_only_replace_none(self):
        self.review_bot.comment_write(only_replace=True,
                                      project=PROJECT, message=COMMENT)
        self.assertFalse(self.comments_filtered(self.bot)[0])

    def comments_filtered(self, bot):
        comments = self.api.get_comments(project_name=PROJECT)
        return self.api.comment_find(comments, bot)

