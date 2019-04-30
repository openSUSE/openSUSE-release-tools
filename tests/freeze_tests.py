import os
import unittest
import difflib
import subprocess
import tempfile

from . import obs

from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.stagingapi import StagingAPI


class TestFreeze(unittest.TestCase):
    def setUp(self):
        """
        Initialize the configuration
        """
        self.obs = obs.OBS()
        Config(obs.APIURL, 'openSUSE:Factory')
        self.api = StagingAPI(obs.APIURL, 'openSUSE:Factory')

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

        fc = FreezeCommand(self.api)

        fp = self._get_fixture_path('staging-meta-for-bootstrap-copy.xml')
        fixture = subprocess.check_output('/usr/bin/xmllint --format %s' % fp, shell=True)

        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(fc.prj_meta_for_bootstrap_copy('openSUSE:Factory:Staging:A'))
        f.close()

        output = subprocess.check_output('/usr/bin/xmllint --format %s' % f.name, shell=True)

        for line in difflib.unified_diff(fixture.split("\n"), output.split("\n")):
            print(line)
        self.assertEqual(output, fixture)
