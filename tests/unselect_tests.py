import unittest
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osclib.unselect_command import UnselectCommand

from obs import APIURL
from obs import PROJECT
from obs import OBS


class TestUnselect(unittest.TestCase):
    def setUp(self):
        self.obs = OBS()
        Config(APIURL, PROJECT)
        self.api = StagingAPI(APIURL, PROJECT)

    def test_cleanup_filter(self):
        UnselectCommand.config_init(self.api)
        UnselectCommand.cleanup_days = 1
        obsolete = self.api.project_status_requests('obsolete', UnselectCommand.filter_obsolete)
        self.assertSequenceEqual(['627445', '642126', '646560', '645723', '646823'], obsolete)
