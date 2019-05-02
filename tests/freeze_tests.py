import os
import unittest
import difflib
import subprocess
import tempfile

from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.stagingapi import StagingAPI
from . import vcrhelpers

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
        wf = vcrhelpers.StagingWorkflow()

        fc = FreezeCommand(wf.api)

        fp = self._get_fixture_path('staging-meta-for-bootstrap-copy.xml')
        fixture = subprocess.check_output('/usr/bin/xmllint --format %s' % fp, shell=True)

        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(fc.prj_meta_for_bootstrap_copy('openSUSE:Factory:Staging:A'))
        f.close()

        output = subprocess.check_output('/usr/bin/xmllint --format %s' % f.name, shell=True)

        for line in difflib.unified_diff(fixture.split("\n"), output.split("\n")):
            print(line)
        self.assertEqual(output, fixture)
