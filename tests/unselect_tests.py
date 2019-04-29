import unittest
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osclib.select_command import SelectCommand
from osclib.unselect_command import UnselectCommand
from osclib.core import package_list_without_links

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

    @my_vcr.use_cassette
    def test_free_staging(self):
        wf = vcrhelpers.StagingWorkflow()
        wf.setup_rings()

        staging_a = wf.create_staging('A', freeze=True)
        winerq = wf.create_submit_request('devel:wine', 'wine')
        self.assertEqual(True, SelectCommand(wf.api, staging_a.name).perform(['wine']))
        self.assertEqual(['wine'], package_list_without_links(wf.apiurl, staging_a.name))

        uc = UnselectCommand(wf.api)
        self.assertIsNone(uc.perform(['wine']))

        self.assertEqual([], package_list_without_links(wf.apiurl, staging_a.name))
