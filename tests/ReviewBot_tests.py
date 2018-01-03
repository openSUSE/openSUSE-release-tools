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

    def comments_filtered(self, bot):
        comments = self.api.get_comments(project_name=PROJECT)
        return self.api.comment_find(comments, bot)

