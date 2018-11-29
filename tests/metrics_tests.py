from OBSLocal import OBSLocalTestCase
import unittest


class TestMetrics(OBSLocalTestCase):
    script = './metrics.py'
    script_debug_osc = False

    def test_all(self):
        self.osc_user('staging-bot')
        self.execute_script(['--help']) # Avoids the need to influxdb instance.
        self.assertOutput('metrics.py')
