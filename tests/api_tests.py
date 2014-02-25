#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import os
import sys
import contextlib
import unittest
import httpretty
import difflib
import subprocess
import tempfile
# mock is part of python3.3
try:
    import unittest.mock
except ImportError:
    import mock

import oscs
import osc
from obs import OBS
import re
import pprint

PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
else:
    string_types = basestring,

class TestApiCalls(unittest.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    def _get_fixtures_dir(self):
        """
        Return path for fixtures
        """
        return os.path.join(os.getcwd(), 'tests/fixtures')

    def _get_fixture_path(self, filename):
        return os.path.join(self._get_fixtures_dir(), filename)

    def _get_fixture_content(self, filename):
        response = open(self._get_fixture_path(filename), 'r')
        content = response.read()
        response.close()
        return content

    def _register_pretty_url_get(self, url, filename):
        """
        Register specified get url with specific filename in fixtures
        :param url: url address to "open"
        :param filename: name of the fixtures file
        """

        content = self._get_fixture_content(filename)

        httpretty.register_uri(httpretty.GET,
                               url,
                               body=content)


    def _register_pretty_url_post(self, url, filename):
        """
        Register specified post url with specific filename in fixtures
        :param url: url address to "open"
        :param filename: name of the fixtures file
        """

        response = open(os.path.join(self._get_fixtures_dir(), filename), 'r')
        content = response.read()
        response.close()

        httpretty.register_uri(httpretty.POST,
                               url,
                               body=content)

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

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Rings:0-Bootstrap',
                                      'ring-0-project.xml')
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Core',
                                      'ring-1-project.xml')

        # Create the api object
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')
        self.assertEqual(ring_packages, api.ring_packages)

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

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/source/openSUSE:Factory:Staging:B/wine',
                                      'linksource.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Compare the results, we only care now that we got 2 of them not the content
        self.assertEqual(package_info,
                         api.get_package_information('openSUSE:Factory:Staging:B', 'wine'))

    @httpretty.activate
    def test_create_package_container(self):
        """
        Test if the uploaded _meta is correct
        """

        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        httpretty.register_uri(
            httpretty.PUT, "http://localhost/source/openSUSE:Factory:Staging:B/wine/_meta")

        api.create_package_container('openSUSE:Factory:Staging:B', 'wine')
        self.assertEqual(httpretty.last_request().method, 'PUT')
        self.assertEqual(httpretty.last_request().body, '<package name="wine"><title/><description/></package>')
        self.assertEqual(httpretty.last_request().path, '/source/openSUSE:Factory:Staging:B/wine/_meta')

        api.create_package_container('openSUSE:Factory:Staging:B', 'wine', disable_build=True)
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
    def test_check_project_status_green(self):
        """
        Test checking project status
        """

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/build/green/_result',
                                      'build-results-green.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Check print output
        self.assertEqual(api.gather_build_status("green"), None)

    @httpretty.activate
    def test_check_project_status_red(self):
        """
        Test checking project status
        """

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/build/red/_result',
                                      'build-results-red.xml')

        # Initiate the api with mocked rings
        with mock_generate_ring_packages():
            api = oscs.StagingAPI('http://localhost')

        # Check print output
        self.assertEqual(api.gather_build_status('red'), ['red', [{'path': 'standard/x86_64', 'state': 'building'}],
                                                          [{'path': 'standard/i586', 'pkg': 'glibc', 'state': 'broken'},
                                                           {'path': 'standard/i586', 'pkg': 'openSUSE-images', 'state': 'failed'}]])

    def test_bootstrap_copy(self):
        import osclib.freeze_command
        fc = osclib.freeze_command.FreezeCommand('http://localhost')

        fp = self._get_fixture_path('staging-meta-for-bootstrap-copy.xml')
        fixture = subprocess.check_output('/usr/bin/xmllint --format %s' % fp, shell=True)

        f = tempfile.NamedTemporaryFile(delete=False)
        f.write(fc.prj_meta_for_bootstrap_copy('openSUSE:Factory:Staging:A'))
        f.close()

        output = subprocess.check_output('/usr/bin/xmllint --format %s' % f.name, shell=True)

        for line in difflib.unified_diff(fixture.split("\n"), output.split("\n")):
            print(line)
        self.assertEqual(output, fixture)


# Here place all mockable functions
@contextlib.contextmanager
def mock_generate_ring_packages():
    with  mock.patch('oscs.StagingAPI._generate_ring_packages', return_value={
        'elem-ring-0': 'openSUSE:Factory:Rings:0-Bootstrap',
        'elem-ring-1': 'openSUSE:Factory:Rings:1-MinimalX'}):
        yield
