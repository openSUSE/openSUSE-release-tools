#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import os
import sys
import contextlib
import unittest
import httpretty
import difflib
import subprocess
import tempfile
# mock is part of python3.3
try:
    import unittest.mock
except ImportError:
    import mock

import oscs
import osc
from obs import OBS
import re
import pprint

class TestFreeze(unittest.TestCase):
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

    def test_bootstrap_copy(self):
        import osclib.freeze_command
        fc = osclib.freeze_command.FreezeCommand('https://localhost')

        fp = self._get_fixture_path('staging-meta-for-bootstrap-copy.xml')
        fixture = subprocess.check_output('/usr/bin/xmllint --format %s' % fp, shell=True)

        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(fc.prj_meta_for_bootstrap_copy('openSUSE:Factory:Staging:A'))
        f.close()

        output = subprocess.check_output('/usr/bin/xmllint --format %s' % f.name, shell=True)

        for line in difflib.unified_diff(fixture.split("\n"), output.split("\n")):
            print(line)
        self.assertEqual(output, fixture)

