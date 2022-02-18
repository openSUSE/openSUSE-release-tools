from osc import oscerr
from osclib.conf import Config
from osclib.core import package_list
from osclib.select_command import SelectCommand
from osclib.unselect_command import UnselectCommand
from osclib.supersede_command import SupersedeCommand
from osclib.ignore_command import IgnoreCommand
from osclib.core import source_file_load
from urllib.error import HTTPError
from lxml import etree as ET
from mock import MagicMock
from . import OBSLocal


class TestSelect(OBSLocal.TestCase):

    def setUp(self):
        super().setUp()
        super(TestSelect, self).setUp()
        self.wf = OBSLocal.FactoryWorkflow()

    def tearDown(self):
        super(TestSelect, self).tearDown()
        del self.wf

    def test_old_frozen(self):
        self.wf.api.prj_frozen_enough = MagicMock(return_value=False)

        # check it won't allow selecting
        staging = self.wf.create_staging('Old')
        self.assertEqual(False, SelectCommand(self.wf.api, staging.name).perform(['gcc']))

    def test_no_matches(self):
        staging = self.wf.create_staging('N', freeze=True)

        # search for requests
        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.wf.api, staging.name).perform(['bash'])
        self.assertEqual(str(cm.exception), "No SR# found for: bash")

    def test_selected(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('S', freeze=True)
        self.wf.create_submit_request('devel:wine', 'wine')

        ret = SelectCommand(self.wf.api, staging.name).perform(['wine'])
        self.assertEqual(True, ret)

    def test_select_multiple_spec(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True)

        project = self.wf.create_project('devel:gcc')
        package = OBSLocal.Package(name='gcc8', project=project)
        package.create_commit(filename='gcc8.spec', text='Name: gcc8')
        package.create_commit(filename='gcc8-tests.spec')
        self.wf.submit_package(package)

        ret = SelectCommand(self.wf.api, staging.name).perform(['gcc8'])
        self.assertEqual(True, ret)

        self.assertEqual(package_list(self.wf.apiurl, staging.name), ['gcc8', 'gcc8-tests'])
        file = source_file_load(self.wf.apiurl, staging.name, 'gcc8', 'gcc8.spec')
        self.assertEqual(file, 'Name: gcc8')
        # we should see the spec file also in the 2nd package
        file = source_file_load(self.wf.apiurl, staging.name, 'gcc8-tests', 'gcc8.spec')
        self.assertEqual(file, 'Name: gcc8')

        uc = UnselectCommand(self.wf.api)
        self.assertIsNone(uc.perform(['gcc8'], False, None))

        # no stale links
        self.assertEqual([], package_list(self.wf.apiurl, staging.name))

    def test_select_multibuild_package(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True)

        project = self.wf.create_project('devel:gcc')
        package = OBSLocal.Package(name='gcc9', project=project)
        package.create_commit(filename='gcc9.spec', text='Name: gcc9')
        package.create_commit(filename='gcc9-tests.spec')
        package.create_commit('<multibuild><flavor>gcc9-tests.spec</flavor></multibuild>', filename='_multibuild')
        self.wf.submit_package(package)

        ret = SelectCommand(self.wf.api, staging.name).perform(['gcc9'])
        self.assertEqual(True, ret)

        self.assertEqual(package_list(self.wf.apiurl, staging.name), ['gcc9'])
        file = source_file_load(self.wf.apiurl, staging.name, 'gcc9', 'gcc9.spec')
        self.assertEqual(file, 'Name: gcc9')

        uc = UnselectCommand(self.wf.api)
        self.assertIsNone(uc.perform(['gcc9'], False, None))

        # no stale links
        self.assertEqual([], package_list(self.wf.apiurl, staging.name))

    def test_supersede(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True)

        rq1 = self.wf.create_submit_request('devel:wine', 'wine')
        SelectCommand(self.wf.api, staging.name).perform(['wine'])
        rq2 = self.wf.create_submit_request('devel:wine', 'wine', text='Something new')
        self.wf.api._packages_staged = None

        self.osc_user('staging-bot')
        Config.get(self.wf.apiurl, self.wf.project)

        SupersedeCommand(self.wf.api).perform()

        self.assertEqual(rq1.reviews(), [{'state': 'accepted', 'by_group': 'factory-staging'}, {'state': 'accepted', 'by_project': 'openSUSE:Factory:Staging:A'},
                                    {'state': 'declined', 'by_group': 'factory-staging'}])
        self.assertEqual(rq2.reviews(), [{'state': 'accepted', 'by_group': 'factory-staging'}, {'state': 'new', 'by_project': 'openSUSE:Factory:Staging:A'}])

    def test_delete_multibuild_package(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True, with_repo=True)

        package = self.wf.create_package(self.wf.project, 'wine')
        package.create_commit('<multibuild><flavor>libs</flavor></multibuild>', filename='_multibuild')

        self.wf.request_package_delete(package)
        ret = SelectCommand(self.wf.api, staging.name).perform(['wine'])
        self.assertEqual(True, ret)

        # TODO: record which URLs were called so we can verify them
        # but we wont' be able to test the actual wipe unless we really build something
        # which is too expensive

    def test_select_excluded_package(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True, with_repo=True)

        self.wf.create_submit_request('devel:wine', 'wine')
        IgnoreCommand(self.wf.api).perform(['wine'])

        with self.assertRaises(HTTPError) as context:
            SelectCommand(self.wf.api, staging.name).perform(['wine'])

        root = ET.fromstring(context.exception.fp.read())
        self.assertRegex(root.find('summary').text, r'Use --remove-exclusion')

    def test_excluded_package(self):
        self.wf.setup_rings()
        staging = self.wf.create_staging('A', freeze=True, with_repo=True)

        self.wf.create_submit_request('devel:wine', 'wine')
        IgnoreCommand(self.wf.api).perform(['wine'])

        ret = SelectCommand(self.wf.api, staging.name).perform(['wine'], remove_exclusion=True)
        self.assertEqual(True, ret)
        self.assertEqual(0, len(self.wf.api.get_ignored_requests()))
