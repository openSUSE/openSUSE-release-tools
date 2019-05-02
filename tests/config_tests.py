import unittest
import vcr
from osc import conf
from osclib.conf import DEFAULT
from osclib.conf import Config
from osclib.core import attribute_value_save
from osclib.memoize import memoize_session_reset
from osclib.stagingapi import StagingAPI

from . import vcrhelpers

my_vcr = vcr.VCR(cassette_library_dir='tests/fixtures/vcr/config')

class TestConfig(unittest.TestCase):
    def setup_vcr(self):
        return vcrhelpers.StagingWorkflow()

    @my_vcr.use_cassette
    def test_basic(self):
        wf = self.setup_vcr()
        self.assertEqual('openSUSE', conf.config[wf.project]['lock-ns'])

    @my_vcr.use_cassette
    def test_remote(self):
        wf = self.setup_vcr()
        # Initial config present in fixtures/oscrc and obs.py attribute default.
        # Local config fixture contains overridden-by-local and should win over
        # the remote config value.
        self.assertEqual('local', conf.config[wf.project]['overridden-by-local'])
        self.assertEqual('remote-indeed', conf.config[wf.project]['remote-only'])

        # Change remote value.
        attribute_value_save(wf.apiurl, wf.project, 'Config', 'remote-only = new value\n')
        wf.load_config()

        self.assertEqual('local', conf.config[wf.project]['overridden-by-local'])
        self.assertEqual('new value', conf.config[wf.project]['remote-only'])

    @my_vcr.use_cassette
    def test_remote_none(self):
        wf = self.setup_vcr()
        wf.load_config('not_real_project')
        self.assertTrue(True) # Did not crash!

    @my_vcr.use_cassette
    def test_pattern_order(self):
        wf = self.setup_vcr()
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
            config = Config(wf.apiurl, project)
            patterns.add(conf.config[project]['pattern'])

        self.assertEqual(len(patterns), len(DEFAULT))

    @my_vcr.use_cassette
    def test_get_memoize_reset(self):
        """Ensure memoize_session_reset() properly forces re-fetch of config."""
        wf = self.setup_vcr()
        self.assertEqual('remote-indeed', Config.get(wf.apiurl, wf.project)['remote-only'])

        attribute_value_save(wf.apiurl, wf.project, 'Config', 'remote-only = new value\n')
        memoize_session_reset()

        self.assertEqual('new value', Config.get(wf.apiurl, wf.project)['remote-only'])
