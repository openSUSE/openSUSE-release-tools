import unittest

from obs import APIURL
from obs import OBS
from osclib.conf import Config
from osclib.check_command import CheckCommand
from osclib.stagingapi import StagingAPI

FULL_REPORT = """
 -- BUILDING Project openSUSE:Factory:Staging:A still needs attention
   - pcre: Missing reviews: repo-checker
   - At least following repositories are still building:
     standard/i586: building
   - Following packages are broken:
     installation-images:Kubic (standard): failed

 -- TESTING Project openSUSE:Factory:Staging:B still needs attention
   - perl-File-Copy-Recursive: Missing reviews: repo-checker
   - Missing check: openqa:cryptlvm

 -- BUILDING Project openSUSE:Factory:Staging:C still needs attention
   - perl: Missing reviews: repo-checker
   - At least following repositories are still building:
     standard/i586: building
   - Missing check: openqa:cryptlvm

 -- FAILED Project openSUSE:Factory:Staging:D still needs attention
   - failure check: openqa:textmode https://openqa.opensuse.org/tests/790715#step/partitioning/2

 ++ Acceptable staging project openSUSE:Factory:Staging:E

 -- FAILED Project openSUSE:Factory:Staging:F still needs attention
   - python-ceilometerclient: Missing reviews: repo-checker
   - Following packages are broken:
     dleyna-server (standard): unresolvable
   - pending check: openqa:textmode https://openqa.opensuse.org/tests/790912

 -- UNACCEPTABLE Project openSUSE:Factory:Staging:adi:16 still needs attention
   - postfixadmin: declined
   - postfixadmin: Missing reviews: repo-checker

 -- UNACCEPTABLE Project openSUSE:Factory:Staging:adi:17 still needs attention
   - cf-cli: declined
   - Following packages are broken:
     cf-cli:test (standard): failed

 -- FAILED Project openSUSE:Factory:Staging:adi:35 still needs attention
   - checkpolicy: Missing reviews: repo-checker
   - Following packages are broken:
     checkpolicy (standard): unresolvable

 -- FAILED Project openSUSE:Factory:Staging:adi:36 still needs attention
   - python-QtPy: Missing reviews: opensuse-review-team
   - Following packages are broken:
     python-QtPy (standard): failed

 -- FAILED Project openSUSE:Factory:Staging:adi:38 still needs attention
   - lmms: Missing reviews: repo-checker
   - Following packages are broken:
     lmms (standard): unresolvable

 -- FAILED Project openSUSE:Factory:Staging:adi:39 still needs attention
   - Following packages are broken:
     ocaml-extlib (standard): unresolvable

 -- FAILED Project openSUSE:Factory:Staging:adi:40 still needs attention
   - apache2-mod_auth_openidc: Missing reviews: repo-checker
   - Following packages are broken:
     apache2-mod_auth_openidc (standard): unresolvable

 -- FAILED Project openSUSE:Factory:Staging:adi:44 still needs attention
   - Following packages are broken:
     nut (standard): failed

 -- FAILED Project openSUSE:Factory:Staging:adi:5 still needs attention
   - verilator: Missing reviews: opensuse-review-team
   - Following packages are broken:
     verilator (standard): unresolvable

 -- FAILED Project openSUSE:Factory:Staging:adi:56 still needs attention
   - Following packages are broken:
     firehol (standard): unresolvable

 -- UNACCEPTABLE Project openSUSE:Factory:Staging:adi:62 still needs attention
   - rubygem-passenger: declined
   - rubygem-passenger: Missing reviews: opensuse-review-team
   - Following packages are broken:
     rubygem-passenger (standard): failed

 -- UNACCEPTABLE Project openSUSE:Factory:Staging:adi:65 still needs attention
   - python-easypysmb: declined
   - python-easypysmb: Missing reviews: opensuse-review-team

 -- FAILED Project openSUSE:Factory:Staging:adi:67 still needs attention
   - cargo-vendor: Missing reviews: repo-checker
   - Following packages are broken:
     cargo-vendor (standard): unresolvable

 -- UNACCEPTABLE Project openSUSE:Factory:Staging:adi:7 still needs attention
   - osslsigncode: declined
   - x86info: Missing reviews: opensuse-review-team
"""

H_REPORT = """
 -- FAILED Project openSUSE:Factory:Staging:H still needs attention
   - failure check: openqa:textmode https://openqa.opensuse.org/tests/790715#step/partitioning/2
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
        self.maxDiff = 20000
        self.assertMultiLineEqual('\n'.join(report).strip(), FULL_REPORT.strip())

    def test_check_command_single(self):
        """Validate json conversion for a single project."""
        report = self.checkcommand._check_project('H')
        self.assertMultiLineEqual('\n'.join(report).strip(), H_REPORT.strip())
