from . import OBSLocal
from osclib.comments import CommentAPI
import random
import re
import unittest


COMMENT = 'short comment'
COMMENT_INFO = {'foo': 'bar', 'distro': 'openSUSE'}
PROJECT = 'openSUSE:Factory:Staging'

class TestComment(unittest.TestCase):
    def setUp(self):
        self.api = CommentAPI('bogus')
        self.bot = type(self).__name__
        self.comments = {
            1: {'comment': '<!-- {} -->\n\nshort comment'.format(self.bot)},
            2: {'comment': '<!-- {} foo=bar distro=openSUSE -->\n\nshort comment'.format(self.bot)}
        }

    def test_truncate(self):
        comment = 'string of text'
        for i in range(len(comment) + 1):
            truncated = self.api.truncate(comment, length=i)
            print(truncated)
            self.assertEqual(len(truncated), i)

    def test_truncate_pre(self):
        comment = """
Some text.

<pre>
bar
mar
car
</pre>

## section 2

<pre>
more
lines
than
you
can
handle
</pre>
""".strip()

        for i in range(len(comment) + len('...\n</pre>')):
            truncated = self.api.truncate(comment, length=i)
            print('=' * 80)
            print(truncated)
            self.assertTrue(len(truncated) <= i, '{} <= {}'.format(len(truncated), i))
            self.assertEqual(truncated.count('<pre>'), truncated.count('</pre>'))
            self.assertFalse(len(re.findall(r'</?\w+[^\w>]', truncated)))
            tag_count = truncated.count('<pre>') + truncated.count('</pre>')
            self.assertEqual(tag_count, truncated.count('<'))
            self.assertEqual(tag_count, truncated.count('>'))

    def test_add_marker(self):
        comment_marked = self.api.add_marker(COMMENT, self.bot)
        self.assertEqual(comment_marked, self.comments[1]['comment'])

        comment_marked = self.api.add_marker(COMMENT, self.bot, COMMENT_INFO)
        self.assertEqual(comment_marked, self.comments[2]['comment'])

    def test_remove_marker(self):
        comment = self.api.remove_marker(COMMENT)
        self.assertEqual(comment, COMMENT)

        comment = self.api.remove_marker(self.comments[1]['comment'])
        self.assertEqual(comment, COMMENT)

        comment = self.api.remove_marker(self.comments[2]['comment'])
        self.assertEqual(comment, COMMENT)

    def test_comment_find(self):
        comment, info = self.api.comment_find(self.comments, self.bot)
        self.assertEqual(comment, self.comments[1])

        comment, info = self.api.comment_find(self.comments, self.bot, COMMENT_INFO)
        self.assertEqual(comment, self.comments[2])
        self.assertEqual(info, COMMENT_INFO)

        info_partial = dict(COMMENT_INFO)
        del info_partial['foo']
        comment, info = self.api.comment_find(self.comments, self.bot, info_partial)
        self.assertEqual(comment, self.comments[2])
        self.assertEqual(info, COMMENT_INFO)


class TestCommentOBS(OBSLocal.TestCase):
    def setUp(self):
        super(TestCommentOBS, self).setUp()
        self.api = CommentAPI(self.apiurl)
        # Ensure different test runs operate in unique namespace.
        self.bot = '::'.join([type(self).__name__, str(random.getrandbits(8))])

    def test_basic(self):
        self.osc_user('staging-bot')

        self.assertFalse(self.comments_filtered(self.bot)[0])

        self.assertTrue(self.api.add_comment(
            project_name=PROJECT, comment=self.api.add_marker(COMMENT, self.bot)))
        comment, _ = self.comments_filtered(self.bot)
        self.assertTrue(comment)

        self.assertTrue(self.api.delete(comment['id']))
        self.assertFalse(self.comments_filtered(self.bot)[0])

    def test_delete_nested(self):
        self.osc_user('staging-bot')
        comment_marked = self.api.add_marker(COMMENT, self.bot)

        # Allow for existing comments by basing assertion on delta from initial count.
        comment_count = len(self.api.get_comments(project_name=PROJECT))
        self.assertFalse(self.comments_filtered(self.bot)[0])

        self.assertTrue(self.api.add_comment(project_name=PROJECT, comment=comment_marked))
        comment, _ = self.comments_filtered(self.bot)
        self.assertTrue(comment)

        for i in range(0, 3):
            self.assertTrue(self.api.add_comment(
                project_name=PROJECT, comment=comment_marked, parent_id=comment['id']))

        comments = self.api.get_comments(project_name=PROJECT)
        parented_count = 0
        for comment in comments.values():
            if comment['parent']:
                parented_count += 1

        self.assertEqual(parented_count, 3)
        self.assertTrue(len(comments) == comment_count + 4)

        self.api.delete_from(project_name=PROJECT)
        self.assertFalse(len(self.api.get_comments(project_name=PROJECT)))

    def test_delete_batch(self):
        users = ['factory-auto', 'repo-checker', 'staging-bot']
        for user in users:
            self.osc_user(user)
            from osc import conf
            bot = '::'.join([self.bot, user])
            comment = self.api.add_marker(COMMENT, bot)

            self.assertFalse(self.comments_filtered(bot)[0])
            self.assertTrue(self.api.add_comment(project_name=PROJECT, comment=comment))
            self.assertTrue(self.comments_filtered(bot)[0])

        # Allow for existing comments by basing assertion on delta from initial count.
        comment_count = len(self.api.get_comments(project_name=PROJECT))
        self.assertTrue(comment_count >= len(users))

        self.api.delete_from_where_user(users[0], project_name=PROJECT)
        self.assertTrue(len(self.api.get_comments(project_name=PROJECT)) == comment_count - 1)

        self.api.delete_from(project_name=PROJECT)
        self.assertFalse(len(self.api.get_comments(project_name=PROJECT)))

    def comments_filtered(self, bot):
        comments = self.api.get_comments(project_name=PROJECT)
        return self.api.comment_find(comments, bot)
