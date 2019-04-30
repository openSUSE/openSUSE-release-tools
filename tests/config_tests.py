import unittest
from osc import conf
from osclib.conf import DEFAULT
from osclib.conf import Config
from osclib.core import attribute_value_save
from osclib.memoize import memoize_session_reset
from osclib.stagingapi import StagingAPI

from . import obs


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.obs = obs.OBS()
        self.load_config()
        self.api = StagingAPI(obs.APIURL, obs.PROJECT)

    def load_config(self, project=obs.PROJECT):
        self.config = Config(obs.APIURL, project)

    def test_basic(self):
        self.assertEqual('openSUSE', conf.config[obs.PROJECT]['lock-ns'])

    def test_remote(self):
        # Initial config present in fixtures/oscrc and obs.py attribute default.
        # Local config fixture contains overridden-by-local and should win over
        # the remote config value.
        self.assertEqual('local', conf.config[obs.PROJECT]['overridden-by-local'])
        self.assertEqual('remote-indeed', conf.config[obs.PROJECT]['remote-only'])

        # Change remote value.
        attribute_value_save(obs.APIURL, obs.PROJECT, 'Config', 'remote-only = new value\n')
        self.load_config()

        self.assertEqual('local', conf.config[obs.PROJECT]['overridden-by-local'])
        self.assertEqual('new value', conf.config[obs.PROJECT]['remote-only'])

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
            'openSUSE:Factory:ARM',
            'openSUSE:Leap:15.1',
            'openSUSE:Leap:15.1:ARM',
            'openSUSE:Leap:15.1:Update',
            'openSUSE:Backports:SLE-15',
            'openSUSE:Backports:SLE-15:Update',
            'SUSE:SLE-15:GA',
            'SUSE:SLE-12:GA',
            'GNOME:Factory',
        )

        # Ensure each pattern is match instead of catch-all pattern.
        patterns = set()
        for project in projects:
            config = Config(obs.APIURL, project)
            patterns.add(conf.config[project]['pattern'])

        self.assertEqual(len(patterns), len(DEFAULT))

    def test_get_memoize_reset(self):
        """Ensure memoize_session_reset() properly forces re-fetch of config."""
        self.assertEqual('remote-indeed', Config.get(obs.APIURL, obs.PROJECT)['remote-only'])

        attribute_value_save(obs.APIURL, obs.PROJECT, 'Config', 'remote-only = new value\n')
        memoize_session_reset()

        self.assertEqual('new value', Config.get(obs.APIURL, obs.PROJECT)['remote-only'])
