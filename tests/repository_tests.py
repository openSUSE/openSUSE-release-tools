import unittest
import os
import os.path
from osclib.core import repository_path_expand

from . import OBSLocal

FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures/repository')

class TestRepository(unittest.TestCase):

    def setUp(self):
        super(TestRepository, self).setUp()
        self.wf = OBSLocal.StagingWorkflow()

    def tearDown(self):
        del self.wf
        super(TestRepository, self).tearDown()

    def add_project(self, name):
        prj = self.wf.create_project(name)
        with open(os.path.join(FIXTURES, name + '.xml')) as f:
            prj.custom_meta(f.read())

    def test_sp5_setup(self):
        self.add_project('SUSE:SLE-12:GA')
        self.add_project('SUSE:SLE-12:Update')
        self.add_project('SUSE:SLE-12-SP1:GA')
        self.add_project('SUSE:SLE-12-SP1:Update')
        self.add_project('SUSE:SLE-12-SP2:GA')
        self.add_project('SUSE:SLE-12-SP2:Update')
        self.add_project('SUSE:SLE-12-SP3:GA')
        self.add_project('SUSE:SLE-12-SP3:Update')
        self.add_project('SUSE:SLE-12-SP4:GA')
        self.add_project('SUSE:SLE-12-SP4:Update')
        self.add_project('SUSE:SLE-12-SP5:GA')
        self.add_project('SUSE:SLE-12-SP5:GA:Staging:A')

        repos = repository_path_expand(self.wf.api.apiurl, 'SUSE:SLE-12-SP5:GA', 'standard')
        self.assertEqual([['SUSE:SLE-12-SP5:GA', 'standard'],
                          ['SUSE:SLE-12-SP4:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP4:GA', 'standard'],
                          ['SUSE:SLE-12-SP3:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP3:GA', 'standard'],
                          ['SUSE:SLE-12-SP2:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP2:GA', 'standard'],
                          ['SUSE:SLE-12-SP1:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP1:GA', 'standard'],
                          ['SUSE:SLE-12:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12:GA', 'standard']], repos)

        repos = repository_path_expand(self.wf.api.apiurl, 'SUSE:SLE-12-SP5:GA:Staging:A', 'standard')
        self.assertEqual([['SUSE:SLE-12-SP5:GA:Staging:A', 'standard'],
                          ['SUSE:SLE-12-SP5:GA', 'ports'],
                          ['SUSE:SLE-12-SP4:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP4:GA', 'standard'],
                          ['SUSE:SLE-12-SP3:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP3:GA', 'standard'],
                          ['SUSE:SLE-12-SP2:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP2:GA', 'standard'],
                          ['SUSE:SLE-12-SP1:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12-SP1:GA', 'standard'],
                          ['SUSE:SLE-12:Update', 'snapshot-SP5'],
                          ['SUSE:SLE-12:GA', 'standard']], repos)
