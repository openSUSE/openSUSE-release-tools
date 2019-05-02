from . import OBSLocal
from . import vcrhelpers
import unittest

class TestDevelProject(OBSLocal.TestCase):
    script = './devel-project.py'
    script_debug_osc = False

    def setUp(self):
        self.wf = vcrhelpers.StagingWorkflow()
        spa = self.wf.create_project('server:php:applications')
        vcrhelpers.Package('drush', project=spa)
        vcrhelpers.Package('drush', self.wf.projects['target'], devel_project='server:php:applications')
        staging = self.wf.create_project('openSUSE:Factory:Staging', maintainer={'users': ['staging-bot']})
        vcrhelpers.Package('dashboard', project=staging)
        self.wf.api.pseudometa_file_ensure('devel_projects', 'server:php:applications')

    def tearDown(self):
        self.osc_user('Admin')
        del self.wf

    def test_list(self):
        self.osc_user('staging-bot')
        self.execute_script(['list', '--write'])
        self.assertOutput('server:php:applications')
        # TODO Assert --write worked and in file.

    def test_reviews(self):
        self.osc_user('staging-bot')
        self.execute_script(['reviews'])

    def test_requests(self):
        self.osc_user('staging-bot')
        self.execute_script(['requests'])
