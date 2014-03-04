#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import sys
import unittest
import httpretty
import mock
import time

from string import Template
from obs import OBS
from osc import oscerr

PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
else:
    string_types = basestring,


class TestApiCalls(unittest.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    def setUp(self):
        """
        Initialize the configuration
        """

        self.obs = OBS()

    @httpretty.activate
    def test_ring_packages(self):
        """
        Validate the creation of the rings.
        """

        # our content in the XML files
        ring_packages = {
            'elem-ring-0': 'openSUSE:Factory:Rings:0-Bootstrap',
            'elem-ring-1': 'openSUSE:Factory:Rings:1-MinimalX',
        }

        # Register OBS
        self.obs.register_obs()
        self.assertEqual(ring_packages, self.obs.api.ring_packages)

    @httpretty.activate
    def test_dispatch_open_requests(self):
        """
        Test dispatching and closure of non-ring packages
        """

        # Register OBS
        self.obs.register_obs()

        # Get rid of open requests
        self.obs.api.dispatch_open_requests()
        # Check that we tried to close it
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'changereviewstate'])
        # Try it again
        self.obs.api.dispatch_open_requests()
        # This time there should be nothing to close
        self.assertEqual(httpretty.last_request().method, 'GET')

    @httpretty.activate
    def test_pseudometa_get_prj(self):
        """
        Test getting project metadata from YAML in project description
        """

        # Register OBS
        self.obs.register_obs()

        # Try to get data from project that has no metadata
        data = self.obs.api.get_prj_pseudometa('openSUSE:Factory:Staging:A')
        # Should be empty, but contain structure to work with
        self.assertEqual(data, {'requests': []})
        # Add some sample data
        rq = { 'id': '123', 'package': 'test-package' }
        data['requests'].append(rq)
        # Save them and read them back
        self.obs.api.set_prj_pseudometa('openSUSE:Factory:Staging:A',data)
        test_data = self.obs.api.get_prj_pseudometa('openSUSE:Factory:Staging:A')
        # Verify that we got back the same data
        self.assertEqual(data,test_data)

    @httpretty.activate
    def test_list_projects(self):
        """
        List projects and their content
        """

        self.obs.register_obs()

        # Prepare expected results
        data = []
        for prj in self.obs.st_project_data:
            data.append('openSUSE:Factory:Staging:' + prj)

        # Compare the results
        self.assertEqual(data, self.obs.api.get_staging_projects())

    @httpretty.activate
    def test_open_requests(self):
        """
        Test searching for open requests
        """

        requests = []

        self.obs.register_obs()

        # get the open requests
        requests = self.obs.api.get_open_requests()

        # Compare the results, we only care now that we got 1 of them not the content
        self.assertEqual(1, len(requests))

    @httpretty.activate
    def test_get_package_information(self):
        """
        Test if we get proper project, name and revision from the staging informations
        """

        package_info = {'project': 'devel:wine',
                        'rev': '7b98ac01b8071d63a402fa99dc79331c',
                        'srcmd5': '7b98ac01b8071d63a402fa99dc79331c',
                        'package': 'wine'}

        self.obs.register_obs()

        # Compare the results, we only care now that we got 2 of them not the content
        self.assertEqual(package_info,
                         self.obs.api.get_package_information('openSUSE:Factory:Staging:B', 'wine'))

    @httpretty.activate
    def test_request_id_package_mapping(self):
        """
        Test whether we can get correct id for sr in staging project
        """

        self.obs.register_obs()

        prj = 'openSUSE:Factory:Staging:B'
        # Get rq
        num = self.obs.api.get_request_id_for_package(prj, 'wine')
        self.assertEqual(333,num)
        # Get package name
        self.assertEqual('wine',self.obs.api.get_package_for_request_id(prj, num))

    @httpretty.activate
    def test_check_one_request(self):
        self.obs.register_obs()

        prj = 'openSUSE:Factory:Staging:B'
        pkg = 'wine'

        # Verify package is there
        self.assertEqual(self.obs.links_data.has_key(prj + '/' + pkg),True)
        # Get rq number
        num = self.obs.api.get_request_id_for_package(prj, pkg)
        # Check the results
        self.assertEqual(self.obs.api.check_one_request(num,prj), None)
        # Pretend to be reviewed by other project
        self.assertEqual(self.obs.api.check_one_request(num,'xyz'),
                         'wine: missing reviews: openSUSE:Factory:Staging:B')

    @httpretty.activate
    def test_check_project_status(self):
        self.obs.register_obs()

        # Check the results
        with mock.patch('oscs.StagingAPI.find_openqa_state', return_value="Nothing"):
            broken_results =  ['At least following repositories is still building:',
                               '    building/x86_64: building',
                               'Following packages are broken:',
                               '    wine (failed/x86_64): failed',
                               '    wine (broken/x86_64): broken',
                               'Nothing']
            self.assertEqual(self.obs.api.check_project_status('openSUSE:Factory:Staging:B'), broken_results)
            self.assertEqual(self.obs.api.check_project_status('openSUSE:Factory:Staging:A'), False)

    @httpretty.activate
    def test_rm_from_prj(self):
        self.obs.register_obs()

        prj = 'openSUSE:Factory:Staging:B'
        pkg = 'wine'

        # Verify package is there
        self.assertEqual(self.obs.links_data.has_key(prj + '/' + pkg),True)
        # Get rq number
        num = self.obs.api.get_request_id_for_package(prj, pkg)
        # Delete the package
        self.obs.api.rm_from_prj(prj, package='wine');
        # Verify package is not there
        self.assertEqual(self.obs.links_data.has_key(prj + '/' + pkg),False)
        # RQ is gone
        self.assertEqual(None, self.obs.api.get_request_id_for_package(prj, pkg))
        self.assertEqual(None, self.obs.api.get_package_for_request_id(prj, num))
        # Verify that review is closed
        self.assertEqual('accepted', self.obs.requests_data[str(num)]['review'])
        self.assertEqual('new', self.obs.requests_data[str(num)]['request'])

        # Try the same with request number
        self.obs.reset_config()
        # Delete the package
        self.obs.api.rm_from_prj(prj, request_id=num);
        # Verify package is not there
        self.assertEqual(self.obs.links_data.has_key(prj + '/' + pkg),False)
        # RQ is gone
        self.assertEqual(None, self.obs.api.get_request_id_for_package(prj, pkg))
        self.assertEqual(None, self.obs.api.get_package_for_request_id(prj, num))
        # Verify that review is closed
        self.assertEqual('accepted', self.obs.requests_data[str(num)]['review'])
        self.assertEqual('new', self.obs.requests_data[str(num)]['request'])

    @httpretty.activate
    def test_add_sr(self):
        self.obs.register_obs()

        prj = 'openSUSE:Factory:Staging:A'
        rq = '123'
        pkg = self.obs.requests_data[rq]['package']

        # Running it twice shouldn't change anything
        for i in [1,2]:
            # Add rq to the project
            self.obs.api.rq_to_prj(rq, prj);
            # Verify that review is there
            self.assertEqual('new', self.obs.requests_data[str(rq)]['review'])
            self.assertEqual('review', self.obs.requests_data[str(rq)]['request'])
            self.assertEqual(self.obs.api.get_prj_pseudometa('openSUSE:Factory:Staging:A'),
                             {'requests': [{'id': 123, 'package': 'gcc'}]})

    @httpretty.activate
    def test_generate_build_status_details(self):
        """
        Check whether generate_build_status_details works
        """

        self.obs.register_obs()
        details_green = self.obs.api.gather_build_status('green')
        details_red = self.obs.api.gather_build_status('red')
        red = ['red', [{'path': 'standard/x86_64', 'state': 'building'}],
                      [{'path': 'standard/i586', 'state': 'broken', 'pkg': 'glibc'},
                       {'path': 'standard/i586', 'state': 'failed', 'pkg': 'openSUSE-images'}]]
        red_result = ['At least following repositories is still building:',
                      '    standard/x86_64: building',
                      'Following packages are broken:',
                      '    glibc (standard/i586): broken',
                      '    openSUSE-images (standard/i586): failed'
                     ]
        self.assertEqual(details_red, red)
        self.assertEqual(self.obs.api.generate_build_status_details(details_red), red_result)
        self.assertEqual(self.obs.api.generate_build_status_details(details_red,True), red_result)
        self.assertEqual(details_green, None)
        self.assertEqual(self.obs.api.generate_build_status_details(details_green), [])

    @httpretty.activate
    def test_create_package_container(self):
        """
        Test if the uploaded _meta is correct
        """

        self.obs.register_obs()

        self.obs.api.create_package_container('openSUSE:Factory:Staging:B', 'wine')
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title/><description/></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

        self.obs.api.create_package_container('openSUSE:Factory:Staging:B', 'wine', disable_build=True)
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title /><description /><build><disable /></build></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

    @httpretty.activate
    def test_review_handling(self):
        """
        Test whether accepting/creating reviews behaves correctly
        """

        # Register OBS
        self.obs.register_obs()

        # Add review
        self.obs.api.add_review('123', by_project='openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'addreview'])
        # Try to readd, should do anything
        self.obs.api.add_review('123', by_project='openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'GET')
        # Accept review
        self.obs.api.set_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'changereviewstate'])
        # Try to accept it again should do anything
        self.obs.api.set_review('123', 'openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'GET')
        # But we should be able to reopen it
        self.obs.api.add_review('123', by_project='openSUSE:Factory:Staging:A')
        self.assertEqual(httpretty.last_request().method, 'POST')
        self.assertEqual(httpretty.last_request().querystring[u'cmd'], [u'addreview'])

    @httpretty.activate
    def test_prj_from_letter(self):
        # Register OBS
        self.obs.register_obs()

        # Verify it works
        self.assertEqual(self.obs.api.prj_from_letter('openSUSE:Factory'), 'openSUSE:Factory')
        self.assertEqual(self.obs.api.prj_from_letter('A'), 'openSUSE:Factory:Staging:A')

    @httpretty.activate
    def test_check_project_status_green(self):
        """
        Test checking project status
        """
        # Register OBS
        self.obs.register_obs()

        # Check print output
        self.assertEqual(self.obs.api.gather_build_status("green"), None)

    @httpretty.activate
    def test_check_project_status_red(self):
        """
        Test checking project status
        """

        # Register OBS
        self.obs.register_obs()

        # Check print output
        self.assertEqual(self.obs.api.gather_build_status('red'),
                        ['red', [{'path': 'standard/x86_64', 'state': 'building'}],
                                [{'path': 'standard/i586', 'pkg': 'glibc', 'state': 'broken'},
                                 {'path': 'standard/i586', 'pkg': 'openSUSE-images', 'state': 'failed'}]])

    @httpretty.activate
    def test_frozen_mtime(self):
        """
        Test frozen mtime
        """

        # Register OBS
        self.obs.register_obs()

        # Testing frozen mtime
        tmpl = Template(self.obs._get_fixture_content('project-a-metalist.xml'))
        self.obs.responses['GET']['/source/openSUSE:Factory:Staging:A/_project'] = tmpl.substitute({'mtime': 1393152777 })

        self.assertTrue(self.obs.api.days_since_last_freeze('openSUSE:Factory:Staging:A') > 8)

        # U == unfrozen
        self.obs.responses['GET']['/source/openSUSE:Factory:Staging:U/_project'] = 'project-u-metalist.xml'
        self.assertTrue(self.obs.api.days_since_last_freeze('openSUSE:Factory:Staging:U') > 1000)

    @httpretty.activate
    def test_select(self):
        """
        Test checking project status
        """

        from osclib.select_command import SelectCommand

        # Register OBS
        self.obs.register_obs()

        # old frozen
        tmpl = Template(self.obs._get_fixture_content('project-a-metalist.xml'))
        self.obs.responses['GET']['/source/openSUSE:Factory:Staging:A/_project'] = tmpl.substitute({'mtime': 1393152777})
        self.assertEqual(False, SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:A', ['bash']))

        # make sure  the project is frozen recently for other tests
        self.obs.responses['GET']['/source/openSUSE:Factory:Staging:A/_project'] = tmpl.substitute({'mtime': str(int(time.time()) - 1000) })

        # search for requests
        self.obs.responses['GET']['/request'] = '<collection matches="0"/>'
        # TODO: it's actually 404 - but OBS class can't handle that ;(
        self.obs.responses['GET']['/request/bash'] = '<collection matches="0"/>'

        with self.assertRaises(oscerr.WrongArgs) as cm:
            SelectCommand(self.obs.api).perform('openSUSE:Factory:Staging:A', ['bash'])

        self.assertEqual(str(cm.exception), "No SR# found for: bash")
