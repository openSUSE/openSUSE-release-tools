from . import OBSLocal
import unittest


class TestDevelProject(OBSLocal.TestCase):
    script = './devel-project.py'
    script_debug_osc = False

    def setUp(self):
        super().setUp()
        self.wf = OBSLocal.FactoryWorkflow()
        spa = self.wf.create_project('server:php:applications')
        OBSLocal.Package('drush', project=spa)
        OBSLocal.Package('drush', self.wf.projects['target'], devel_project='server:php:applications')
        staging = self.wf.create_project('openSUSE:Factory:Staging', maintainer={'users': ['staging-bot']})
        OBSLocal.Package('dashboard', project=staging)
        self.wf.api.pseudometa_file_ensure('devel_projects', 'server:php:applications')

    def tearDown(self):
        super().tearDown()
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
