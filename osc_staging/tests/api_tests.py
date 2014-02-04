#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import os
import contextlib
import unittest
import httpretty

import oscs
import osc

class TestApiCalls(unittest.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """

    def _get_fixtures_dir(self):
        """
        Return path for fixtures
        """
        return os.path.join(os.getcwd(), 'tests/fixtures')

    def _register_pretty_url_get(self, url, filename):
        """
        Register specified url with specific filename in fixtures
        :param url: url address to "open"
        :param filename: name of the fixtures file
        """

        response = open(os.path.join(self._get_fixtures_dir(), filename), 'r')
        content = response.read()
        response.close()

        httpretty.register_uri(httpretty.GET,
                               url,
                               body=content)

    def setUp(self):
        """
        Initialize the configuration so the osc is happy
        """

        oscrc = os.path.join(self._get_fixtures_dir(), 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        os.environ['OSC_CONFIG'] = oscrc

    @httpretty.activate
    def test_list_projects(self):
        """
        List projects and their content
        """
        prjlist = [
            'openSUSE:Factory:Staging:A',
            'openSUSE:Factory:Staging:B',
            'openSUSE:Factory:Staging:C',
            'openSUSE:Factory:Staging:D'
        ]

        # Initiate the pretty overrides
        self._register_pretty_url_get('http://localhost/search/project/id?match=starts-with(@name,\'openSUSE:Factory:Staging:\')',
                                      'staging-project-list.xml')

        # Ensure the output is equal to what we expect
        self.assertEqual(prjlist,
                         oscs.list_staging_projects('http://localhost'))
