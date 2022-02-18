import unittest

from osclib.accept_command import AcceptCommand
from osclib.select_command import SelectCommand
from osclib.conf import Config
from osclib.comments import CommentAPI
from osclib.stagingapi import StagingAPI
from osclib.core import package_list

from mock import MagicMock
from . import OBSLocal

class TestAccept(unittest.TestCase):

    def setup_wf(self):
        wf = OBSLocal.FactoryWorkflow()
        wf.setup_rings()

        self.c_api = CommentAPI(wf.api.apiurl)

        staging_b = wf.create_staging('B', freeze=True)
        self.prj = staging_b.name

        self.winerq = wf.create_submit_request('devel:wine', 'wine', text='Hallo World')
        self.assertEqual(True, SelectCommand(wf.api, self.prj).perform(['wine']))
        self.comments = self.c_api.get_comments(project_name=self.prj)
        wf.create_attribute_type('OSRT', 'ProductVersion', 1)
        return wf

    def test_accept_comments(self):
        wf = self.setup_wf()

        self.assertEqual(True, AcceptCommand(wf.api).accept_all(['B']))

        # Comments are cleared up
        accepted_comments = self.c_api.get_comments(project_name=self.prj)
        self.assertEqual(len(accepted_comments), 0)

    def test_accept_final_comment(self):
        wf = self.setup_wf()

        # snipe out cleanup to see the comments before the final countdown
        wf.api.staging_deactivate = MagicMock(return_value=True)

        self.assertEqual(True, AcceptCommand(wf.api).accept_all(['B']))

        comments = self.c_api.get_comments(project_name=self.prj)
        self.assertGreater(len(comments), len(self.comments))

        # check which id was added
        new_id = (set(comments.keys()) - set(self.comments.keys())).pop()
        comment = comments[new_id]['comment']
        self.assertEqual('Project "{}" accepted. The following packages have been submitted to openSUSE:Factory: wine.'.format(self.prj), comment)

    def test_accept_new_multibuild_package(self):
        wf = self.setup_wf()

        staging = wf.create_staging('A', freeze=True)

        project = wf.create_project('devel:gcc')
        package = OBSLocal.Package(name='gcc9', project=project)
        package.create_commit(filename='gcc9.spec')
        package.create_commit(filename='gcc9-tests.spec')
        package.create_commit('<multibuild><flavor>gcc9-tests.spec</flavor></multibuild>', filename='_multibuild')
        wf.submit_package(package)

        SelectCommand(wf.api, staging.name).perform(['gcc9'])
        ac = AcceptCommand(wf.api)
        self.assertEqual(True, ac.accept_all(['A'], True))

        # no stale links
        self.assertEqual([], package_list(wf.apiurl, staging.name))
        self.assertEqual(['gcc9', 'wine'], package_list(wf.apiurl, wf.project))

    def test_accept_new_multispec_package(self):
        wf = self.setup_wf()

        staging = wf.create_staging('A', freeze=True)

        project = wf.create_project('devel:gcc')
        package = OBSLocal.Package(name='gcc9', project=project)
        package.create_commit(filename='gcc9.spec')
        package.create_commit(filename='gcc9-tests.spec')
        wf.submit_package(package)

        SelectCommand(wf.api, staging.name).perform(['gcc9'])
        ac = AcceptCommand(wf.api)
        self.assertEqual(True, ac.accept_all(['A'], True))

        # no stale links
        self.assertEqual([], package_list(wf.apiurl, staging.name))
        self.assertEqual(['gcc9', 'gcc9-tests', 'wine'], package_list(wf.apiurl, wf.project))

    def test_accept_switch_to_multibuild_package(self):
        wf = self.setup_wf()

        staging = wf.create_staging('A', freeze=True)

        tpackage = wf.create_package('target', 'gcc9')
        tpackage.create_commit(filename='gcc9.spec')
        tpackage.create_commit(filename='gcc9-tests.spec')
        lpackage = wf.create_package('target', 'gcc9-tests')
        lpackage.create_commit('<link package="gcc9" cicount="copy" />', filename='_link')

        project = wf.create_project('devel:gcc')
        package = OBSLocal.Package(name='gcc9', project=project)
        package.create_commit(filename='gcc9.spec')
        package.create_commit(filename='gcc9-tests.spec')
        package.create_commit('<multibuild><flavor>gcc9-tests.spec</flavor></multibuild>', filename='_multibuild')

        wf.submit_package(package)

        SelectCommand(wf.api, staging.name).perform(['gcc9'])
        ac = AcceptCommand(wf.api)
        self.assertEqual(True, ac.accept_all(['A'], True))

        # no stale links
        self.assertEqual([], package_list(wf.apiurl, staging.name))
        self.assertEqual(['gcc9', 'wine'], package_list(wf.apiurl, wf.project))
