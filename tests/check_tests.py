import unittest

from obs import APIURL
from obs import OBS
from osclib.conf import Config
from osclib.check_command import CheckCommand
from osclib.stagingapi import StagingAPI

FULL_REPORT = """
 ++ Acceptable staging project openSUSE:Factory:Staging:A

 ++ Acceptable staging project openSUSE:Factory:Staging:C

 -- REVIEW Project openSUSE:Factory:Staging:F still needs attention
   - yast2-iscsi-client: Missing reviews: factory-repo-checker

 -- REVIEW Project openSUSE:Factory:Staging:G still needs attention
   - Mesa: Missing reviews: opensuse-review-team

 -- BUILDING Project openSUSE:Factory:Staging:H still needs attention
   - kiwi: Missing reviews: opensuse-review-team
   - At least following repositories are still building:
     standard/i586: building
   - openQA's overall status is failed for https://openqa.opensuse.org/tests/10660
     first_boot: fail

 -- REVIEW Project openSUSE:Factory:Staging:J still needs attention
   - jeuclid: Missing reviews: factory-repo-checker
"""

H_REPORT = """
 -- BUILDING Project openSUSE:Factory:Staging:H still needs attention
   - kiwi: Missing reviews: opensuse-review-team
   - At least following repositories are still building:
     standard/i586: scheduling
     standard/x86_64: building
     images/x86_64: blocked
   - openQA's overall status is failed for https://openqa.opensuse.org/tests/10660
     livecdreboot: fail
"""


class TestCheckCommand(unittest.TestCase):
    """Tests CheckCommand."""

    def setUp(self):
        """Initialize the configuration."""

        self.obs = OBS()
        Config(APIURL, 'openSUSE:Factory')
        self.stagingapi = StagingAPI(APIURL, 'openSUSE:Factory')
        self.checkcommand = CheckCommand(self.stagingapi)

    def test_check_command_all(self):
        """Validate json conversion for all projects."""
        report = self.checkcommand._check_project()
        self.assertEqual('\n'.join(report).strip(), FULL_REPORT.strip())

    def test_check_command_single(self):
        """Validate json conversion for a single project."""
        report = self.checkcommand._check_project('H')
        self.assertEqual('\n'.join(report).strip(), H_REPORT.strip())
