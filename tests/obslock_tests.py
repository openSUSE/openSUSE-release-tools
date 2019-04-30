from datetime import datetime
import unittest
from osclib.conf import Config
from osclib.obslock import OBSLock
from . import vcrhelpers

class TestOBSLock(unittest.TestCase):

    def obs_lock(self, wf, reason='list'):
        return OBSLock(wf.apiurl, wf.project, reason=reason)

    def assertLockFail(self, lock):
        with self.assertRaises(SystemExit):
            with lock:
                self.assertFalse(lock.locked)

    def test_lock(self):
        wf = self.setup_vcr()
        lock = self.obs_lock(wf)
        self.assertFalse(lock.locked)

        with lock:
            self.assertTrue(lock.locked)

            user, reason, reason_sub, ts = lock._parse(lock._read())
            self.assertIsNotNone(user)
            self.assertEqual(reason, 'list')
            self.assertIsNone(reason_sub)
            self.assertIsInstance(ts, datetime)

        self.assertFalse(lock.locked)

    def test_locked_self(self):
        wf = self.setup_vcr()
        self.locked_self(wf, hold=False)

    def locked_self(self, wf, hold=False):
        lock1 = self.obs_lock(wf)
        lock2 = self.obs_lock(wf)

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
        wf = self.setup_vcr()
        lock = self.obs_lock(wf, 'lock')
        self.assertFalse(lock.locked)

        with lock:
            self.assertTrue(lock.locked)
            lock.hold('test')

        self.assertTrue(lock.locked)

        # Same constraints should apply since same user against hold.
        self.locked_self(wf, hold=True)

        # Hold should remain after subcommands are executed.
        user, reason, reason_sub, ts = lock._parse(lock._read())
        self.assertIsNotNone(user)
        self.assertEqual(reason, 'hold: test')
        self.assertIsNone(reason_sub)
        self.assertIsInstance(ts, datetime)

        # Other users should not bypass hold.
        lock_user2 = self.obs_lock(wf)
        lock_user2.user = 'other'
        self.assertLockFail(lock_user2)

        lock.release(force=True)

        self.assertFalse(lock.locked)

    def test_expire(self):
        wf = self.setup_vcr()
        lock1 = self.obs_lock(wf)
        lock2 = self.obs_lock(wf)
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

    def test_expire_hold(self):
        wf = self.setup_vcr()
        lock1 = self.obs_lock(wf, 'lock')
        lock2 = self.obs_lock(wf, 'override')
        lock2.ttl = 0
        lock2.user = 'user2'

        self.assertFalse(lock1.locked)
        self.assertFalse(lock2.locked)

        with lock1:
            self.assertTrue(lock1.locked)
            lock1.hold('test')
            with lock2:
                self.assertTrue(lock2.locked)
                user, reason, reason_sub, _ = lock2._parse(lock2._read())
                self.assertEqual(user, lock2.user)
                self.assertEqual(reason, 'override')
                self.assertEqual(reason_sub, None, 'does not inherit hold')

    def setup_vcr(self):
        wf = vcrhelpers.StagingWorkflow()
        wf.create_target()
        # we should most likely create this as part of create_target, but
        # it just slows down all other tests
        wf.create_attribute_type('openSUSE', 'LockedBy')
        wf.create_project(wf.project + ':Staging')
        return wf

    def test_reserved_characters(self):
        wf = self.setup_vcr()
        lock = self.obs_lock(wf, 'some reason @ #night')

        with lock:
            _, reason, _, _ = lock._parse(lock._read())
            self.assertEqual(reason, 'some reason at hashnight')

    def test_needed(self):
        wf = self.setup_vcr()
        lock1 = self.obs_lock(wf)
        lock2 = self.obs_lock(wf, 'unlock')
        lock2.user = 'user2'
        lock2.needed = False

        self.assertFalse(lock1.locked)
        self.assertFalse(lock2.locked)

        with lock1:
            self.assertTrue(lock1.locked)
            with lock2:
                self.assertFalse(lock2.locked)
                user, _, _, _ = lock2._parse(lock2._read())
                self.assertEqual(user, lock1.user, 'lock1 remains')

                lock2.release(force=True)
                user, _, _, _ = lock2._parse(lock2._read())
                self.assertEqual(user, None, 'unlocked')
