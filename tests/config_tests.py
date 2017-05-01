import unittest
from osc import conf
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

from obs import APIURL
from obs import OBS

PROJECT = 'openSUSE:Factory'


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.obs = OBS()
        self.config = Config(PROJECT)

    def test_basic(self):
        self.assertEqual('openSUSE', conf.config[PROJECT]['lock-ns'])

    def test_remote(self):
        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertIsNone(conf.config[PROJECT].get('remote-only'))

        api = StagingAPI(APIURL, PROJECT)
        self.config.apply_remote(api)

        self.assertEqual('local', conf.config[PROJECT]['overridden-by-local'])
        self.assertEqual('remote-indeed', conf.config[PROJECT]['remote-only'])
