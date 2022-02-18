import unittest

from osclib.check_command import CheckCommand

from lxml import etree
from mock import MagicMock
from . import OBSLocal

H_REPORT = """
 -- FAILED Project openSUSE:Factory:Staging:H still needs attention
   - neon: Missing reviews: group:origin-reviewers
   - Following packages are broken:
     git (standard): unresolvable
   - failure check: openqa:kde https://openqa.opensuse.org/tests/1077669#step/dolphin/5
"""


class TestCheckCommand(unittest.TestCase):
    """Tests CheckCommand."""

    def test_check_command_single(self):
        """Validate json conversion for a single project."""

        wf = OBSLocal.FactoryWorkflow()
        wf.create_staging('H')
        self.checkcommand = CheckCommand(wf.api)

        with open('tests/fixtures/project/staging_projects/openSUSE:Factory/H.xml', encoding='utf-8') as f:
            xml = etree.fromstring(f.read())
            wf.api.project_status = MagicMock(return_value=xml)
        report = self.checkcommand._check_project('openSUSE:Factory:Staging:H')
        self.assertMultiLineEqual('\n'.join(report).strip(), H_REPORT.strip())
