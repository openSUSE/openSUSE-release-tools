#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright (C) 2014 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import unittest

from obs import APIURL
from obs import OBS
from osclib.checkrepo import CheckRepo


class TestCheckRepoCalls(unittest.TestCase):
    """Tests for various check repo calls."""

    def setUp(self):
        """Initialize the configuration."""

        self.obs = OBS()
        self.checkrepo = CheckRepo(APIURL)
        # Des-memoize some functions
        self.checkrepo.build = self.checkrepo._build
        self.checkrepo.last_build_success = self.checkrepo._last_build_success

    def test_packages_grouping(self):
        """Validate the creation of the groups."""
        grouped = {
            1000: 'openSUSE:Factory:Staging:J',
            1001: 'openSUSE:Factory:Staging:J',
            501: 'openSUSE:Factory:Staging:C',
            502: 'openSUSE:Factory:Staging:C',
            333: 'openSUSE:Factory:Staging:B'
        }
        groups = {
            'openSUSE:Factory:Staging:J': [1000, 1001],
            'openSUSE:Factory:Staging:C': [501, 502],
            'openSUSE:Factory:Staging:B': [333]
        }
        self.assertEqual(self.checkrepo.grouped, grouped)
        self.assertEqual(self.checkrepo.groups, groups)

    def test_pending_request(self):
        """Test CheckRepo.get_request."""
        self.assertEqual(len(self.checkrepo.pending_requests()), 2)

    def test_check_specs(self):
        """Test CheckRepo.check_specs."""
        for request in self.checkrepo.pending_requests():
            request_and_specs = self.checkrepo.check_specs(request=request)
            self.assertEqual(len(request_and_specs), 1)
            self.assertTrue(request_and_specs[0].request_id in (1000, 1001))
        for request_id in (1000, 1001):
            request_and_specs = self.checkrepo.check_specs(request_id=request_id)
            self.assertEqual(len(request_and_specs), 1)
            self.assertEqual(request_and_specs[0].request_id, request_id)

    def test_repos_to_check(self):
        """Test CheckRepo.repositories_to_check."""
        for request in self.checkrepo.pending_requests():
            request_and_specs = self.checkrepo.check_specs(request=request)
            for rq_or_spec in request_and_specs:
                print self.checkrepo.repositories_to_check(rq_or_spec)
