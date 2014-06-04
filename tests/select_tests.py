#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import sys
import unittest
import httpretty
import time

from obs import APIURL
from obs import OBS
from osc import oscerr
from osclib.select_command import SelectCommand
from oscs import StagingAPI


class TestSelect(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """
        self.obs = OBS()
        self.api = StagingAPI(APIURL)

    def test_old_frozen(self):
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), False)
        self.assertEqual(True, SelectCommand(self.api).perform('openSUSE:Factory:Staging:A', ['gcc']))
        self.assertEqual(self.api.prj_frozen_enough('openSUSE:Factory:Staging:A'), True)

    def test_no_matches(self):
        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.api).perform('openSUSE:Factory:Staging:B', ['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    def test_selected(self):
        # make sure the project is frozen recently for other tests

        ret = SelectCommand(self.api).perform('openSUSE:Factory:Staging:B', ['wine'])
        self.assertEqual(True, ret)
