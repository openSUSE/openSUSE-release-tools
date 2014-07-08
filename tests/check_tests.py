#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2014 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import unittest

from obs import APIURL
from obs import OBS
from osclib.check_command import CheckCommand
from osclib.stagingapi import StagingAPI

FULL_REPORT = """
 ++ Acceptable staging project openSUSE:Factory:Staging:A

 ++ Acceptable staging project openSUSE:Factory:Staging:B

 ++ Acceptable staging project openSUSE:Factory:Staging:C

 ++ Acceptable staging project openSUSE:Factory:Staging:D

 ++ Acceptable staging project openSUSE:Factory:Staging:E

 -- Project openSUSE:Factory:Staging:F still neeeds attention
   - yast2-iscsi-client: Missing reviews: factory-repo-checker

 -- Project openSUSE:Factory:Staging:G still neeeds attention
   - Mesa: Missing reviews: opensuse-review-team

 -- Project openSUSE:Factory:Staging:H still neeeds attention
   - kiwi: Missing reviews: opensuse-review-team
   - At least following repositories are still building:
     standard/i586: building
   - openQA's overall status is failed for https://openqa.opensuse.org/tests/10660

 -- For subproject openSUSE:Factory:Staging:H:DVD
   - At least following repositories are still building:
     standard/x86_64: blocked

 ++ Acceptable staging project openSUSE:Factory:Staging:I

 -- Project openSUSE:Factory:Staging:J still neeeds attention
   - jeuclid: Missing reviews: factory-repo-checker
"""

H_REPORT = """
 -- Project openSUSE:Factory:Staging:H still neeeds attention
   - kiwi: Missing reviews: opensuse-review-team
   - At least following repositories are still building:
     standard/i586: scheduling
     standard/x86_64: building
     images/x86_64: blocked
   - openQA's overall status is failed for https://openqa.opensuse.org/tests/10660

 -- For subproject openSUSE:Factory:Staging:H:DVD
   - At least following repositories are still building:
     standard/x86_64: blocked
     images/x86_64: blocked
"""


class TestCheckCommand(unittest.TestCase):
    """Tests CheckCommand."""

    def setUp(self):
        """Initialize the configuration."""

        self.obs = OBS()
        self.stagingapi = StagingAPI(APIURL)
        self.checkcommand = CheckCommand(self.stagingapi)

    def test_check_command_all(self):
        """Validate json conversion for all projects."""
        report = self.checkcommand._check_project()
        self.assertEqual('\n'.join(report).strip(), FULL_REPORT.strip())

    def test_check_command_single(self):
        """Validate json conversion for a single project."""
        report = self.checkcommand._check_project('H')
        self.assertEqual('\n'.join(report).strip(), H_REPORT.strip())
