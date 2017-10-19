from OBSLocal import OBSLocalTestCase
import unittest


class TestDevelProject(OBSLocalTestCase):
    script = './devel-project.py'
    script_debug_osc = False

    def test_list(self):
        self.osc_user('staging-bot')
        self.execute(['list', '--write'])
        self.assertOutput('server:php:applications')
        # TODO Assert --write worked and in file.

    def test_reviews(self):
        self.osc_user('staging-bot')
        self.execute(['reviews'])

    @unittest.skip('#1205: devel-project: requests subcommand broken due to osc 0.160.0')
    def test_requests(self):
        self.osc_user('staging-bot')
        self.execute(['requests'])
