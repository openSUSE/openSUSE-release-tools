#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import sys
import unittest
import httpretty
import time

from string import Template
from obs import OBS
from osc import oscerr

class TestSelect(unittest.TestCase):
    def setUp(self):
        """
        Initialize the configuration
        """

        self.obs = OBS()

    def _get_fixture_path(self, filename):
        """
        Return path for fixture
        """
        return os.path.join(self._get_fixtures_dir(), filename)

    def _get_fixtures_dir(self):
        """
        Return path for fixtures
        """
        return os.path.join(os.getcwd(), 'tests/fixtures')

    @httpretty.activate
    def test_select(self):
        """
        Test checking project status
        """

        from osclib.select_command import SelectCommand

        # Register OBS
        self.obs.register_obs()

        # old frozen
        tmpl = Template(self.obs._get_fixture_content('project-a-metalist.xml'))
        self.obs.responses['GET']['/source/openSUSE:Factory:Staging:A/_project'] = tmpl.substitute({'mtime': 1393152777})
        self.assertEqual(False, SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:A', ['bash']))
        self.assertEqual(sys.stdout.getvalue(), "Freeze the prj first\n")

        # make sure  the project is frozen recently for other tests
        self.obs.responses['GET']['/source/openSUSE:Factory:Staging:A/_project'] = tmpl.substitute({'mtime': str(int(time.time()) - 1000) })

        # search for requests
        self.obs.responses['GET']['/request'] = '<collection matches="0"/>'
        self.obs.responses['GET']['/request/bash'] = {'status': 404, 'reply': '<collection matches="0"/>' }

        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:A', ['bash'])

        self.assertEqual(str(cm.exception), "No SR# found for: bash")
