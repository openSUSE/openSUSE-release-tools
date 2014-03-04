#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import sys
import unittest
import httpretty
import time

from obs import OBS
from osc import oscerr
from osclib.select_command import SelectCommand

class TestSelect(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """

        self.obs = OBS()

    @httpretty.activate
    def test_old_frozen(self):
        self.obs.register_obs()
        self.assertEqual(False, SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:A', ['bash']))
        self.assertEqual(sys.stdout.getvalue(), "Freeze the prj first\n")

    @httpretty.activate
    def test_no_matches(self):
        self.obs.register_obs()

        # search for requests
        self.obs.responses['GET']['/request'] = '<collection matches="0"/>'
        self.obs.responses['GET']['/request/bash'] = {'status': 404, 'reply': '<collection matches="0"/>' }

        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:B', ['bash'])

        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    @httpretty.activate
    def test_selected(self):
        self.obs.register_obs()
        # make sure the project is frozen recently for other tests

        self.obs.responses['GET']['/request'] = '<collection matches="1"><request id="123"></request></collection>'
        ret = SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:B', ['bash'])
        self.assertEqual(True, ret)
