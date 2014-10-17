#!/usr/bin/python

import unittest
import logging
from check_source_in_factory import Checker

from obs import APIURL
from obs import OBS

class TestFactorySourceAccept(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """
        self.obs = OBS()

        logging.basicConfig()
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.DEBUG)

        self.checker = Checker(apiurl = APIURL, \
                user = 'test-reviewer', \
                logger = self.logger)

    def test_accept_request(self):

        self.checker.set_request_ids(['770001'])
        self.checker.check_requests()

if __name__ == '__main__':
    unittest.main()

# vim: sw=4 et
