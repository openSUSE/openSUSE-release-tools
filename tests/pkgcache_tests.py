#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 aplanas@suse.de, openSUSE.org
# Distribute under GPLv2 or later

import os
import shutil
import unittest

from mock import MagicMock

import osclib.pkgcache
from osclib.pkgcache import PkgCache


class TestPkgCache(unittest.TestCase):
    def setUp(self):
        """Initialize the environment."""
        self.cache = PkgCache('/tmp/cache', force_clean=True)
        for fn in ('file_a', 'file_b', 'file_c'):
            with open(os.path.join('/tmp', fn), 'w') as f:
                print >>f, fn

    def tearDown(self):
        """Clean the environment."""
        shutil.rmtree('/tmp/cache')
        for fn in ('/tmp/file_a', '/tmp/file_b', '/tmp/file_c'):
            os.unlink(fn)

    def test_insertion(self):
        self.cache[('file_a', 1)] = '/tmp/file_a'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        self.assertEqual(open('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a').read(), 'file_a\n')
        self.cache[('file_b', 1)] = '/tmp/file_b'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/a7/004efbb89078ebcc8f21d55354e2f3'))
        self.assertEqual(open('/tmp/cache/pkgcache/a7/004efbb89078ebcc8f21d55354e2f3').read(), 'file_b\n')
        self.cache[('file_c', 1)] = '/tmp/file_c'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/22/ee05516c08f3672cb25e03ce7f045f'))
        self.assertEqual(open('/tmp/cache/pkgcache/22/ee05516c08f3672cb25e03ce7f045f').read(), 'file_c\n')

    def test_index(self):
        self.cache[('file_a', 1)] = '/tmp/file_a'
        self.assertEqual(self.cache[('file_a', 1)], ('c7f33375edf32d8fb62d4b505c74519a', 'file_a'))
        self.cache[('file_b', 1)] = '/tmp/file_b'
        self.assertEqual(self.cache[('file_b', 1)], ('a7004efbb89078ebcc8f21d55354e2f3', 'file_b'))
        self.cache[('file_c', 1)] = '/tmp/file_c'
        self.assertEqual(self.cache[('file_c', 1)], ('22ee05516c08f3672cb25e03ce7f045f', 'file_c'))
        self.assertEqual(set(self.cache.keys()), set((('file_a', 1), ('file_b', 1), ('file_c', 1))))

    def test_delete(self):
        self.cache[('file_a', 1)] = '/tmp/file_a'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        del self.cache[('file_a', 1)]
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7'))
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache'))

        self.cache[('file_b', 1)] = '/tmp/file_b'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/a7/004efbb89078ebcc8f21d55354e2f3'))
        del self.cache[('file_b', 1)]
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/a7/004efbb89078ebcc8f21d55354e2f3'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/a7'))
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache'))

        self.cache[('file_c', 1)] = '/tmp/file_c'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/22/ee05516c08f3672cb25e03ce7f045f'))
        del self.cache[('file_c', 1)]
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/22/ee05516c08f3672cb25e03ce7f045f'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/22'))
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache'))

    def test_collision(self):
        self.cache[('file_a', 1)] = '/tmp/file_a'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        self.cache[('file_a', 2)] = '/tmp/file_a'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-001'))
        self.cache[('file_a', 3)] = '/tmp/file_a'
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-002'))

        del self.cache[('file_a', 2)]
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-001'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-002'))

        del self.cache[('file_a', 1)]
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-001'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-002'))

        del self.cache[('file_a', 3)]
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-001'))
        self.assertFalse(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a-002'))

    def test_linkto(self):
        self.cache[('file_a', 1)] = '/tmp/file_a'
        self.cache.linkto(('file_a', 1), '/tmp/file_a_')
        self.assertEqual(open('/tmp/file_a_').read(), 'file_a\n')

        os.unlink('/tmp/file_a_')
        self.assertTrue(os.path.exists('/tmp/cache/pkgcache/c7/f33375edf32d8fb62d4b505c74519a'))

    def test_clean(self):
        self.cache[('file_a', 1)] = '/tmp/file_a'
        self.cache[('file_a', 2)] = '/tmp/file_a'
        self.cache[('file_a', 3)] = '/tmp/file_a'

        self.cache[('file_b', 1)] = '/tmp/file_b'
        self.cache[('file_c', 1)] = '/tmp/file_c'

        osclib.pkgcache.time.time = MagicMock(return_value=3)
        self.cache._clean_cache(ttl=2)
        self.assertFalse(('file_a', 1) in self.cache)
        self.assertFalse(('file_a', 2) in self.cache)
        self.assertTrue(('file_a', 3) in self.cache)
        self.assertFalse(('file_b', 1) in self.cache)
        self.assertFalse(('file_c', 1) in self.cache)

if __name__ == '__main__':
    unittest.main()
