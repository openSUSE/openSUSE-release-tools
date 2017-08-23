from osclib.comments import CommentAPI
import re
import unittest


class TestComment(unittest.TestCase):
    def setUp(self):
        self.api = CommentAPI('bogus')

    def test_truncate(self):
        comment = "string of text"
        for i in xrange(len(comment) + 1):
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

        for i in xrange(len(comment) + len('...\n</pre>')):
            truncated = self.api.truncate(comment, length=i)
            print('=' * 80)
            print(truncated)
            self.assertTrue(len(truncated) <= i, '{} <= {}'.format(len(truncated), i))
            self.assertEqual(truncated.count('<pre>'), truncated.count('</pre>'))
            self.assertFalse(len(re.findall(r'</?\w+[^\w>]', truncated)))
            tag_count = truncated.count('<pre>') + truncated.count('</pre>')
            self.assertEqual(tag_count, truncated.count('<'))
            self.assertEqual(tag_count, truncated.count('>'))
