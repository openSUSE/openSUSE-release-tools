import unittest
from osc import conf
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

    def test_remote_none(self):
        self.api.dashboard_content_save('config', '')
        self.assertEqual(self.obs.dashboard_counts['config'], 1)
        self.config.apply_remote(self.api)
        # Ensure blank file not overridden.
        self.assertEqual(self.obs.dashboard_counts['config'], 1)
