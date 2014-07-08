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
 -- Project openSUSE:Factory:Staging:A still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10576
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10575
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10574

 -- For subproject openSUSE:Factory:Staging:A:DVD
 -- Project openSUSE:Factory:Staging:A:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10674
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10673
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10672

 -- Project openSUSE:Factory:Staging:B still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10521
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10520
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10519

 -- For subproject openSUSE:Factory:Staging:B:DVD
 -- Project openSUSE:Factory:Staging:B:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10524
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10523
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10522

 -- Project openSUSE:Factory:Staging:C still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10193
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10158
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10157

 -- For subproject openSUSE:Factory:Staging:C:DVD
 -- Project openSUSE:Factory:Staging:C:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10458
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10457
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10162

 -- Project openSUSE:Factory:Staging:D still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10570
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10569
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10568

 -- For subproject openSUSE:Factory:Staging:D:DVD
 -- Project openSUSE:Factory:Staging:D:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10573
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10572
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10571

 -- Project openSUSE:Factory:Staging:E still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10603
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10602
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10601

 -- For subproject openSUSE:Factory:Staging:E:DVD
 -- Project openSUSE:Factory:Staging:E:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10658
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10657
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10656

 -- Project openSUSE:Factory:Staging:F still neeeds attention
   - yast2-iscsi-client: Missing reviews: factory-repo-checker
   - yast2-storage: Missing reviews: factory-repo-checker
   - zypper: Missing reviews: factory-repo-checker
   - libzypp: Missing reviews: factory-repo-checker
   - yast2-nfs-server: Missing reviews: factory-repo-checker
   - yast2: Missing reviews: factory-repo-checker
   - libyui-qt-pkg: Missing reviews: factory-repo-checker
   - libstorage: Missing reviews: factory-repo-checker
   - libqt5-qtbase: Missing reviews: factory-repo-checker
   - autoyast2: Missing reviews: opensuse-review-team
   - autoyast2: Missing reviews: factory-repo-checker
   - yast2-pkg-bindings: Missing reviews: opensuse-review-team
   - yast2-pkg-bindings: Missing reviews: factory-repo-checker
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10637
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10636
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10635

 -- For subproject openSUSE:Factory:Staging:F:DVD
 -- Project openSUSE:Factory:Staging:F:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10641
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10640
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10638

 -- Project openSUSE:Factory:Staging:G still neeeds attention
   - Mesa: Missing reviews: opensuse-review-team
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10631
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10630
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10629

 -- For subproject openSUSE:Factory:Staging:G:DVD
 -- Project openSUSE:Factory:Staging:G:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10634
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10633
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10632

 -- Project openSUSE:Factory:Staging:H still neeeds attention
   - kiwi: Missing reviews: opensuse-review-team
   - At least following repositories are still building:
     standard: building
     standard: building
     images: blocked
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10661
   - openQA's overall status is failed for https://openqa.opensuse.org/tests/10660
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10659

 -- For subproject openSUSE:Factory:Staging:H:DVD
 -- Project openSUSE:Factory:Staging:H:DVD still neeeds attention
   - At least following repositories are still building:
     standard: blocked
     images: blocked
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10665
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10664
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10662

 -- Project openSUSE:Factory:Staging:I still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10517
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10464
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10462

 -- For subproject openSUSE:Factory:Staging:I:DVD
 -- Project openSUSE:Factory:Staging:I:DVD still neeeds attention
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10467
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10466
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10465

 -- Project openSUSE:Factory:Staging:J still neeeds attention
   - jeuclid: Missing reviews: factory-repo-checker
   - libcss: Missing reviews: factory-repo-checker
   - scilab: Missing reviews: factory-repo-checker
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/9637
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/9636
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/9635
"""

H_REPORT = """
 -- Project openSUSE:Factory:Staging:H still neeeds attention
   - kiwi: Missing reviews: opensuse-review-team
   - At least following repositories are still building:
     standard: scheduling
     standard: building
     images: blocked
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10661
   - openQA's overall status is failed for https://openqa.opensuse.org/tests/10660
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10659

 -- For subproject openSUSE:Factory:Staging:H:DVD
 -- Project openSUSE:Factory:Staging:H:DVD still neeeds attention
   - At least following repositories are still building:
     standard: blocked
     images: blocked
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10665
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10664
   - openQA's overall status is passed for https://openqa.opensuse.org/tests/10662
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
