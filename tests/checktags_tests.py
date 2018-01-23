# -*- coding: utf-8 -*-
# Copyright (C) 2015 SUSE Linux GmbH
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
#
#
# To run this test manually, go to the parent directory and run:
# LANG=C python tests/checktags_tests.py

import os
import unittest
import logging
import httpretty
import osc
import urlparse
import sys
import re
from osclib.cache import Cache
from check_tags_in_requests import TagChecker

sys.path.append(".")

APIURL = 'https://maintenancetest.example.com'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')


class TestTagChecker(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """

        Cache.last_updated[APIURL] = {'__oldest': '2016-12-18T11:49:37Z'}
        httpretty.reset()
        httpretty.enable()

        oscrc = os.path.join(FIXTURES, 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        # osc.conf.config['debug'] = 1

        logging.basicConfig()
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.DEBUG)

        self.checker = TagChecker(apiurl=APIURL,
                                  user='maintbot',
                                  logger=self.logger)

        self._request_data = """
                <request id="293129" creator="darix">
                  <action type="submit">
                    <source project="editors" package="nano" rev="25"/>
                    <target project="openSUSE:Factory" package="nano"/>
                  </action>
                  <state name="review" who="factory-auto" when="2015-03-25T16:24:59">
                    <comment>Please review build success</comment>
                  </state>
                  <review state="accepted" when="2015-03-25T16:24:32" who="licensedigger" by_group="legal-auto">
                    <comment></comment>
                    <history who="licensedigger" when="2015-03-25T16:30:13">
                      <description>Review got accepted</description>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="factory-auto" by_group="factory-auto">
                    <comment>Check script succeeded</comment>
                    <history who="factory-auto" when="2015-03-25T16:24:59">
                      <description>Review got accepted</description>
                      <comment>Check script succeeded</comment>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="coolo" by_group="factory-staging">
                    <comment>No need for staging, not in tested ring projects.</comment>
                    <history who="coolo" when="2015-03-25T20:47:33">
                      <description>Review got accepted</description>
                      <comment>No need for staging, not in tested ring projects.</comment>
                    </history>
                  </review>
                  <review state="new" by_user="maintbot">
                    <comment>Please review sources</comment>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:59" who="factory-repo-checker" by_user="factory-repo-checker">
                    <comment>Builds for repo editors/openSUSE_Factory</comment>
                    <history who="factory-repo-checker" when="2015-03-25T18:28:47">
                      <description>Review got accepted</description>
                      <comment>Builds for repo editors/openSUSE_Factory</comment>
                    </history>
                  </review>
                </request>
            """
        self._request_withhistory = """
                <request id="293129" creator="darix">
                  <action type="submit">
                    <source project="editors" package="nano" rev="25"/>
                    <target project="openSUSE:Factory" package="nano"/>
                  </action>
                  <state name="review" who="factory-auto" when="2015-03-25T16:24:59">
                    <comment>Please review build success</comment>
                  </state>
                  <review state="accepted" when="2015-03-25T16:24:32" who="licensedigger" by_group="legal-auto">
                    <comment></comment>
                    <history who="licensedigger" when="2015-03-25T16:30:13">
                      <description>Review got accepted</description>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="factory-auto" by_group="factory-auto">
                    <comment>Check script succeeded</comment>
                    <history who="factory-auto" when="2015-03-25T16:24:59">
                      <description>Review got accepted</description>
                      <comment>Check script succeeded</comment>
                    </history>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:32" who="coolo" by_group="factory-staging">
                    <comment>No need for staging, not in tested ring projects.</comment>
                    <history who="coolo" when="2015-03-25T20:47:33">
                      <description>Review got accepted</description>
                      <comment>No need for staging, not in tested ring projects.</comment>
                    </history>
                  </review>
                  <review state="new" by_user="maintbot">
                    <comment>Please review sources</comment>
                  </review>
                  <review state="accepted" when="2015-03-25T16:24:59" who="factory-repo-checker" by_user="factory-repo-checker">
                    <comment>Builds for repo editors/openSUSE_Factory</comment>
                    <history who="factory-repo-checker" when="2015-03-25T18:28:47">
                      <description>Review got accepted</description>
                      <comment>Builds for repo editors/openSUSE_Factory</comment>
                    </history>
                  </review>
                  <history who="darix" when="2015-03-25T16:24:32">
                    <description>Request created</description>
                  </history>
                  <history who="factory-auto" when="2015-03-25T16:24:59">
                    <description>Request got a new review request</description>
                    <comment>Please review sources</comment>
                  </history>
                  <history who="factory-auto" when="2015-03-25T16:24:59">
                    <description>Request got a new review request</description>
                    <comment>Please review build success</comment>
                  </history>
                </request>
            """
        self._nano_meta = """<package name="nano" project="openSUSE:Factory">
  <title>Pico Editor Clone with Enhancements</title>
  <description>GNU nano is a small and friendly text editor. It aims to emulate the
Pico text editor while also offering a few enhancements.</description>
  <devel project="editors" package="nano"/>
</package>"""

    def _run_with_data(self, accept, exists_in_factory, issues_data):
        # exists_in_factory: whether the package is exists in factory
        httpretty.register_uri(httpretty.POST,
                               osc.core.makeurl(APIURL, ['source', "editors", "nano"], {         'cmd': 'diff',
                                                                                                 'onlyissues': '1',
                                                                                                 'view': 'xml',
                                                                                                 'opackage': 'nano',
                                                                                                 'oproject': 'openSUSE:Factory',
                                                                                                 'rev': '25'}),
                               match_querystring=True,
                               body=issues_data)
        httpretty.register_uri(httpretty.GET,
                               osc.core.makeurl(APIURL, ['source', "editors", "nano"], {'rev': '25', 'view': 'info'}),
                               match_querystring=True,
                               body="""<sourceinfo package="nano" rev="25" vrev="35" srcmd5="aa7cce4956a86aee36c3f38aa37eee2b" lsrcmd5="c26618f949f5869cabcd6f989fb040ca" verifymd5="fc6b5b47f112848a1eb6fb8660b7800b"><filename>nano.spec</filename><linked project="openSUSE:Factory" package="nano" /></sourceinfo>""")

        if exists_in_factory is True:
            httpretty.register_uri(httpretty.GET,
                                   osc.core.makeurl(APIURL, ['source', "openSUSE:Factory", "nano", '_meta'], {}),
                                   match_querystring=True,
                                   body=self._nano_meta)
            httpretty.register_uri(httpretty.GET,
                                   osc.core.makeurl(APIURL, ['source', "openSUSE:Factory", "nano"], {'view': 'info'}),
                                   match_querystring=True,
                                   body="""<sourceinfo package="nano" rev="25" vrev="35" srcmd5="aa7cce4956a86aee36c3f38aa37eee2b" lsrcmd5="c26618f949f5869cabcd6f989fb040ca" verifymd5="fc6b5b47f112848a1eb6fb8660b7800b"><filename>nano.spec</filename><linked project="openSUSE:Factory" package="nano" /></sourceinfo>""")
        else:
            httpretty.register_uri(httpretty.GET,
                                   osc.core.makeurl(APIURL, ['source', "openSUSE:Factory", "nano", '_meta'], {}),
                                   status=404,
                                   match_querystring=True,
                                   body="")
            httpretty.register_uri(httpretty.GET,
                                   osc.core.makeurl(APIURL, ['source', "openSUSE:Factory", "nano"], {'view': 'info'}),
                                   status=404,
                                   match_querystring=True,
                                   body="")

        httpretty.register_uri(httpretty.GET,
                               APIURL + "/request/293129",
                               match_querystring=True,
                               body=self._request_data)
        httpretty.register_uri(httpretty.GET,
                               APIURL + "/request/293129?withhistory=1",
                               match_querystring=True,
                               body=self._request_withhistory)

        httpretty.register_uri(httpretty.GET,
                               re.compile(re.escape(APIURL + "/search/request?")),
                               match_querystring=True,
                               body='<collection matches="0"></collection>')

        result = {'state_accepted': None}

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'newstate=accepted&cmd=changereviewstate&by_user=maintbot':
                result['state_accepted'] = True
            elif u.query == 'newstate=declined&cmd=changereviewstate&by_user=maintbot':
                result['state_accepted'] = False
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
                               APIURL + "/request/293129",
                               body=lambda method, uri, headers: change_request(result, method, uri, headers))

        self.checker.set_request_ids(['293129'])
        self.checker.check_requests()

        self.assertEqual(result['state_accepted'], accept)

    def test_1_issue_accept(self):
        # a new package and has issues
        self._run_with_data(True, False, """<sourcediff key="4ecfa5c08d7765060b4fa248aab3c7e7">
  <old project="home:snwint:sle12-sp1" package="perl-Bootloader" rev="4" srcmd5="bb554c82d62186fa4c4440ba36651028" />
  <new project="SUSE:SLE-12-SP1:GA" package="perl-Bootloader" rev="23" srcmd5="231d457675a9fca041b22d84df9d4464" />
  <files />
  <issues>
    <issue state="changed" tracker="bnc" name="151877" label="boo#151877" url="https://bugzilla.suse.com/show_bug.cgi?id=151877" />
  </issues>
</sourcediff>""")

    def test_3_issues_accept(self):
        # not a new package and has issues
        # changes already in Factory
        self._run_with_data(True, True, """<sourcediff key="4ecfa5c08d7765060b4fa248aab3c7e7">
  <old project="home:snwint:sle12-sp1" package="perl-Bootloader" rev="4" srcmd5="bb554c82d62186fa4c4440ba36651028" />
  <new project="SUSE:SLE-12-SP1:GA" package="perl-Bootloader" rev="23" srcmd5="231d457675a9fca041b22d84df9d4464" />
  <files />
  <issues>
    <issue state="changed" tracker="bnc" name="151877" label="boo#151877" url="https://bugzilla.suse.com/show_bug.cgi?id=151877" />
    <issue state="changed" tracker="fate" name="110038" label="fate#110038" url="https://fate.suse.com/110038" />
    <issue state="deleted" tracker="bnc" name="831791" label="boo#831791" url="https://bugzilla.suse.com/show_bug.cgi?id=831791" />
  </issues>
</sourcediff>""")

    def test_no_issues_decline(self):
        # a new package and has without issues
        self._run_with_data(False, False, """<sourcediff key="4ecfa5c08d7765060b4fa248aab3c7e7">
  <old project="home:snwint:sle12-sp1" package="perl-Bootloader" rev="4" srcmd5="bb554c82d62186fa4c4440ba36651028" />
  <new project="SUSE:SLE-12-SP1:GA" package="perl-Bootloader" rev="23" srcmd5="231d457675a9fca041b22d84df9d4464" />
  <files />
  <issues/>
</sourcediff>""")

    def test_no_issues_tag_decline(self):
        # a new package and has without issues tag
        self._run_with_data(False, False, """<sourcediff key="4ecfa5c08d7765060b4fa248aab3c7e7">
  <old project="home:snwint:sle12-sp1" package="perl-Bootloader" rev="4" srcmd5="bb554c82d62186fa4c4440ba36651028" />
  <new project="SUSE:SLE-12-SP1:GA" package="perl-Bootloader" rev="23" srcmd5="231d457675a9fca041b22d84df9d4464" />
  <files />
</sourcediff>""")

    def test_no_issues_accept(self):
        # not a new package and has without issues
        # changes already in Factory
        self._run_with_data(True, True, """<sourcediff key="4ecfa5c08d7765060b4fa248aab3c7e7">
  <old project="home:snwint:sle12-sp1" package="perl-Bootloader" rev="4" srcmd5="bb554c82d62186fa4c4440ba36651028" />
  <new project="SUSE:SLE-12-SP1:GA" package="perl-Bootloader" rev="23" srcmd5="231d457675a9fca041b22d84df9d4464" />
  <files />
  <issues/>
</sourcediff>""")

    def test_no_issues_tag_accept(self):
        # not a new package and has without issues tag
        # changes already in Factory
        self._run_with_data(True, True, """<sourcediff key="4ecfa5c08d7765060b4fa248aab3c7e7">
  <old project="home:snwint:sle12-sp1" package="perl-Bootloader" rev="4" srcmd5="bb554c82d62186fa4c4440ba36651028" />
  <new project="SUSE:SLE-12-SP1:GA" package="perl-Bootloader" rev="23" srcmd5="231d457675a9fca041b22d84df9d4464" />
  <files />
</sourcediff>""")


if __name__ == '__main__':
    unittest.main()

