import unittest
from osc import conf
from osclib.conf import DEFAULT
from osclib.conf import Config
from osclib.core import attribute_value_save
from osclib.memoize import memoize_session_reset
from osclib.stagingapi import StagingAPI

from obs import APIURL
from obs import PROJECT
from obs import OBS


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.obs = OBS()
        self.load_config()
        self.api = StagingAPI(APIURL, PROJECT)

    def load_config(self, project=PROJECT):
        self.config = Config(APIURL, project)

    def test_basic(self):
        self.assertEqual('openSUSE', conf.config[PROJECT]['lock-ns'])

    def test_remote(self):
        # Initial config present in fixtures/oscrc and obs.py attribute default.
        # Local config fixture contains overridden-by-local and should win over
        # the remote config value.
        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertEqual('remote-indeed', conf.config[PROJECT]['remote-only'])

        # Change remote value.
        attribute_value_save(APIURL, PROJECT, 'Config', 'remote-only = new value\n')
        self.load_config()

        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertEqual('new value', conf.config[PROJECT]['remote-only'])

    def test_remote_none(self):
        self.load_config('not_real_project')
        self.assertTrue(True) # Did not crash!

    def test_pattern_order(self):
        # Add pattern to defaults in order to identify which was matched.
        for pattern in DEFAULT:
            DEFAULT[pattern]['pattern'] = pattern

        # A list of projects that should match each of the DEFAULT patterns.
        projects = (
            'openSUSE:Factory',
            'openSUSE:Leap:15.0',
            'openSUSE:Leap:15.0:Update',
            'openSUSE:Backports:SLE-15',
            'SUSE:SLE-15:GA',
            'SUSE:SLE-12:GA',
            'GNOME:Factory',
        )

        # Ensure each pattern is match instead of catch-all pattern.
        patterns = set()
        for project in projects:
            config = Config(APIURL, project)
            patterns.add(conf.config[project]['pattern'])

        self.assertEqual(len(patterns), len(DEFAULT))

    def test_get_memoize_reset(self):
        """Ensure memoize_session_reset() properly forces re-fetch of config."""
        self.assertEqual('remote-indeed', Config.get(APIURL, PROJECT)['remote-only'])

        attribute_value_save(APIURL, PROJECT, 'Config', 'remote-only = new value\n')
        memoize_session_reset()

        self.assertEqual('new value', Config.get(APIURL, PROJECT)['remote-only'])
