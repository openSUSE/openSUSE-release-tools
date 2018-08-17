import unittest
from osc import conf
from osclib.conf import DEFAULT
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

from obs import APIURL
from obs import PROJECT
from obs import OBS


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.obs = OBS()
        self.config = Config(PROJECT)
        self.api = StagingAPI(APIURL, PROJECT)

    def test_basic(self):
        self.assertEqual('openSUSE', conf.config[PROJECT]['lock-ns'])

    def test_remote(self):
        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertIsNone(conf.config[PROJECT].get('remote-only'))

        self.config.apply_remote(self.api)

        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertEqual('remote-indeed', conf.config[PROJECT]['remote-only'])

        self.api.attribute_value_save('Config', 'remote-only = nope')
        self.config.apply_remote(self.api)

        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertEqual('nope', conf.config[PROJECT]['remote-only'])

    def test_remote_none(self):
        self.api.attribute_value_save('Config', '')
        # don't crash
        self.config.apply_remote(self.api)

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
            config = Config(project)
            patterns.add(conf.config[project]['pattern'])

        self.assertEqual(len(patterns), len(DEFAULT))
