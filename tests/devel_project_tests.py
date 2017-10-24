from OBSLocal import OBSLocalTestCase
import unittest


class TestDevelProject(OBSLocalTestCase):
    script = './devel-project.py'
    script_debug_osc = False

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
