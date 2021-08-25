import unittest
from . import OBSLocal

class TestBuildFailReminder(OBSLocal.TestCase):
    script = './build-fail-reminder.py'

    def test_basic(self):
        self.wf = OBSLocal.FactoryWorkflow()
        self.wf.create_target()

        self.execute_script(['--relay', 'smtp', '--sender', 'Tester'])
        self.assertOutput('loading build fails for openSUSE:Factory')
