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
        obsolete = self.api.project_status_requests('obsolete', UnselectCommand.filter_obsolete)
        self.assertTrue('492438' in obsolete, 'revoked')
        self.assertTrue('592437' in obsolete, 'superseded but over threshold')
        self.assertTrue('492439' in obsolete, 'declined by leaper')
        self.assertTrue('492441' in obsolete, 'declined but over threshold')
        self.assertEqual(len(obsolete), 4)
