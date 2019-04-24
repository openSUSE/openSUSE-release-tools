import unittest
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osclib.unselect_command import UnselectCommand

import vcr
from . import vcrhelpers

my_vcr = vcr.VCR(cassette_library_dir='tests/fixtures/vcr/unselect')

class TestUnselect(unittest.TestCase):

    @my_vcr.use_cassette
    def test_cleanup_filter(self):
        wf = vcrhelpers.StagingWorkflow()
        UnselectCommand.config_init(wf.api)
        UnselectCommand.cleanup_days = 1
        obsolete = wf.api.project_status_requests('obsolete', UnselectCommand.filter_obsolete)
        self.assertSequenceEqual([], obsolete)
