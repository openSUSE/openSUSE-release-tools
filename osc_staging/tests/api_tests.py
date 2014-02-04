#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import contextlib
import imp
import unittest
import mock

oscs = imp.load_source('oscs', '../osc-staging.py')

class TestApiCalls(unittest.TestCase):
    """
    Tests for various api calls to ensure we return expected content
    """


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
        with mock_http_GET('staging-project-list.xml'):
            self.assertEqual(prjlist,
                        oscs._list_staging_projects('https://api.opensuse.org'))


@contextlib.contextmanager
def mock_http_GET(url):
    with mock.patch('osc.core.http_GET', return_value=open('./fixtures/{0}'.format(url), 'r')):
        yield
