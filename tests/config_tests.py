import unittest
import vcr
from osc import conf
from osclib.conf import DEFAULT
from osclib.conf import Config
from osclib.core import attribute_value_save
from osclib.memoize import memoize_session_reset
from osclib.stagingapi import StagingAPI

from vcrhelpers import APIURL, PROJECT, StagingWorkflow

my_vcr = vcr.VCR(cassette_library_dir='tests/fixtures/vcr/config')

class TestConfig(unittest.TestCase):
    def setup_vcr(self):
        self.wf = StagingWorkflow()
        self.wf.setup_remote_config()

    def load_config(self, project=PROJECT):
        self.wf.load_config(project)

    @my_vcr.use_cassette
    def test_basic(self):
        self.setup_vcr()
        self.assertEqual('openSUSE', conf.config[PROJECT]['lock-ns'])

    @my_vcr.use_cassette
    def test_remote(self):
        self.setup_vcr()
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

    @my_vcr.use_cassette
    def test_remote_none(self):
        self.setup_vcr()
        self.load_config('not_real_project')
        self.assertTrue(True) # Did not crash!

    @my_vcr.use_cassette
    def test_pattern_order(self):
        self.setup_vcr()
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

    @my_vcr.use_cassette
    def test_get_memoize_reset(self):
        """Ensure memoize_session_reset() properly forces re-fetch of config."""
<<<<<<< HEAD
        self.assertEqual('remote-indeed', Config.get(obs.APIURL, obs.PROJECT)['remote-only'])
=======
        self.setup_vcr()
        self.assertEqual('remote-indeed', Config.get(APIURL, PROJECT)['remote-only'])
>>>>>>> cf6a774... Use factories and vcr

        attribute_value_save(obs.APIURL, obs.PROJECT, 'Config', 'remote-only = new value\n')
        memoize_session_reset()

        self.assertEqual('new value', Config.get(obs.APIURL, obs.PROJECT)['remote-only'])
