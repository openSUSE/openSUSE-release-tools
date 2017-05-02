from datetime import datetime
import unittest
from osclib.conf import Config
from osclib.obslock import OBSLock

from obs import APIURL
from obs import PROJECT
from obs import OBS


class TestOBSLock(unittest.TestCase):
    def setUp(self):
        self.obs = OBS()
        Config(PROJECT)

    def obs_lock(self, reason='list'):
        return OBSLock(APIURL, PROJECT, reason=reason)

    def assertLockFail(self, lock):
        with self.assertRaises(SystemExit):
            with lock:
                self.assertFalse(lock.locked)

    def test_lock(self):
        lock = self.obs_lock()
        self.assertFalse(lock.locked)

        with lock:
            self.assertTrue(lock.locked)

            user, reason, reason_sub, ts = lock._parse(lock._read())
            self.assertIsNotNone(user)
            self.assertEqual(reason, 'list')
            self.assertIsNone(reason_sub)
            self.assertIsInstance(ts, datetime)

        self.assertFalse(lock.locked)

    def test_locked_self(self, hold=False):
        lock1 = self.obs_lock()
        lock2 = self.obs_lock()

        self.assertFalse(lock1.locked)
        self.assertFalse(lock2.locked)

        with lock1:
            self.assertTrue(lock1.locked)
            self.assertLockFail(lock2)

        if not hold:
            # A hold will remain locked.
            self.assertFalse(lock1.locked)
        self.assertFalse(lock2.locked)

    def test_hold(self):
        lock = self.obs_lock('lock')
        self.assertFalse(lock.locked)

        with lock:
            self.assertTrue(lock.locked)
            lock.hold('test')

        self.assertTrue(lock.locked)

        # Same constraints should apply since same user against hold.
        self.test_locked_self(hold=True)

        # Hold should remain after subcommands are executed.
        user, reason, reason_sub, ts = lock._parse(lock._read())
        self.assertIsNotNone(user)
        self.assertEqual(reason, 'hold: test')
        self.assertIsNone(reason_sub)
        self.assertIsInstance(ts, datetime)

        # Other users should not bypass hold.
        lock_user2 = self.obs_lock()
        lock_user2.user = 'other'
        self.assertLockFail(lock_user2)

        lock.release(force=True)

        self.assertFalse(lock.locked)

    def test_expire(self):
        lock1 = self.obs_lock()
        lock2 = self.obs_lock()
        lock2.ttl = 0
        lock2.user = 'user2'

        self.assertFalse(lock1.locked)
        self.assertFalse(lock2.locked)

        with lock1:
            self.assertTrue(lock1.locked)
            with lock2:
                self.assertTrue(lock2.locked)
                user, _, _, _ = lock2._parse(lock2._read())
                self.assertEqual(user, lock2.user)

    def test_reserved_characters(self):
        lock = self.obs_lock('some reason @ #night')

        with lock:
            _, reason, _, _ = lock._parse(lock._read())
            self.assertEqual(reason, 'some reason at hashnight')
