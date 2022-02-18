import unittest
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osclib.unselect_command import UnselectCommand
from . import OBSLocal


class TestUnselect(OBSLocal.TestCase):

    def test_cleanup_filter(self):
        wf = OBSLocal.FactoryWorkflow()
        UnselectCommand.config_init(wf.api)
        UnselectCommand.cleanup_days = 1
        obsolete = wf.api.project_status_requests('obsolete', UnselectCommand.filter_obsolete)
        self.assertSequenceEqual([], obsolete)

    # most testing for unselect happens in select_tests
