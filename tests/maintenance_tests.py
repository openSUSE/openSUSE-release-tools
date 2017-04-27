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

import os
import unittest
import logging
import httpretty
import osc
import re
import urlparse

from check_maintenance_incidents import MaintenanceChecker

APIURL = 'https://maintenancetest.example.com'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')


def rr(s):
    return re.compile(re.escape(APIURL + s))


class TestMaintenance(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """

        httpretty.reset()
        httpretty.enable()

        oscrc = os.path.join(FIXTURES, 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        #osc.conf.config['debug'] = 1

        logging.basicConfig()
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.DEBUG)

        self.checker = MaintenanceChecker(apiurl = APIURL, \
                user = 'maintbot', \
                logger = self.logger)

    def test_non_maintainer_submit(self):
        """same as above but already has devel project as reviewer
        """

        httpretty.register_uri(httpretty.GET,
            rr("/search/request?withhistory=1&match=state%2F%40name%3D%27review%27+and+review%5B%40by_user%3D%27maintbot%27+and+%40state%3D%27new%27%5D"),
            match_querystring = True,
            body = """
                <collection matches="1">
                  <request id="261355" creator="brassh">
                    <action type="maintenance_incident">
                      <source project="home:brassh" package="mysql-workbench" rev="857c77d2ba1d347b6dc50a1e5bcb74e1"/>
                      <target project="openSUSE:Maintenance" releaseproject="openSUSE:13.2:Update"/>
                    </action>
                    <state name="review" who="lnussel_factory" when="2014-11-13T10:46:52">
                      <comment></comment>
                    </state>
                    <review state="new" by_user="maintbot">
                      <comment></comment>
                    </review>
                    <history who="brassh" when="2014-11-13T09:18:19">
                      <description>Request created</description>
                      <comment>...</comment>
                    </history>
                    <history who="lnussel_factory" when="2014-11-13T10:46:52">
                      <description>Request got a new review request</description>
                    </history>
                    <description>...</description>
                  </request>
                </collection>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/261355",
            match_querystring = True,
            body = """
              <request id="261355" creator="brassh">
                <action type="maintenance_incident">
                  <source project="home:brassh" package="mysql-workbench" rev="857c77d2ba1d347b6dc50a1e5bcb74e1"/>
                  <target project="openSUSE:Maintenance" releaseproject="openSUSE:13.2:Update"/>
                </action>
                <state name="review" who="lnussel_factory" when="2014-11-13T10:46:52">
                  <comment></comment>
                </state>
                <review state="new" by_user="maintbot">
                  <comment></comment>
                </review>
                <history who="brassh" when="2014-11-13T09:18:19">
                  <description>Request created</description>
                  <comment>...</comment>
                </history>
                <history who="lnussel_factory" when="2014-11-13T10:46:52">
                  <description>Request got a new review request</description>
                </history>
                <description>...</description>
              </request>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/source/home:brassh/mysql-workbench",
            match_querystring = True,
            body = """
                <directory name="mysql-workbench" rev="6" vrev="6" srcmd5="858204decf53f923d5574dbe6ae63b15">
                  <linkinfo project="openSUSE:13.2" package="mysql-workbench" srcmd5="ed9c3b12388cbd14868eb3faabe34685" baserev="ed9c3b12388cbd14868eb3faabe34685" xsrcmd5="08bfb4f40cb1e2de8f9cd4633bf02eb1" lsrcmd5="858204decf53f923d5574dbe6ae63b15" />
                  <serviceinfo code="succeeded" xsrcmd5="6ec4305a8e5363e26a7f4895a0ae12d2" />
                  <entry name="_link" md5="85ef5fb38ca1ec7c300311fda9f4b3d1" size="121" mtime="1414567341" />
                  <entry name="mysql-workbench-community-6.1.7-src.tar.gz" md5="ac059e239869fb77bf5d7a1f5845a8af" size="24750696" mtime="1404405925" />
                  <entry name="mysql-workbench-ctemplate.patch" md5="06ccba1f8275cd9408f515828ecede19" size="1322" mtime="1404658323" />
                  <entry name="mysql-workbench-glib.patch" md5="67fd7d8e3503ce0909381bde747c8a1e" size="1785" mtime="1415732509" />
                  <entry name="mysql-workbench-mysql_options4.patch" md5="9c07dfe1b94af95daf3e16bd6a161684" size="910" mtime="1404658324" />
                  <entry name="mysql-workbench-no-check-for-updates.patch" md5="1f0c9514ff8218d361ea46d3031b2b64" size="1139" mtime="1404658324" />
                  <entry name="mysql-workbench.changes" md5="26bc54777e6a261816b72f64c69630e4" size="13354" mtime="1415747835" />
                  <entry name="mysql-workbench.spec" md5="88b562a93f01b842a5798f809e3c8188" size="7489" mtime="1415745943" />
                  <entry name="openSUSE_(Vendor_Package).xml" md5="ab041af98d7748c216e7e5787ec36f65" size="743" mtime="1315923090" />
                  <entry name="patch-desktop-categories.patch" md5="c24b3283573c34a5e072be122388f8e1" size="391" mtime="1376991147" />
                </directory>
            """)

        result = { 'devel_review_added' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'by_package=mysql-workbench&cmd=addreview&by_project=server%3Adatabase':
                result['devel_review_added'] = True
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/261355",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        httpretty.register_uri(httpretty.GET,
            APIURL + "/search/owner?project=openSUSE:13.2:Update&binary=mysql-workbench",
            match_querystring = True,
            body = """
                <collection/>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/search/owner?binary=mysql-workbench",
            match_querystring = True,
            body = """
                <collection>
                  <owner rootproject="openSUSE" project="server:database" package="mysql-workbench">
                    <person name="Gankov" role="maintainer"/>
                    <person name="bruno_friedmann" role="maintainer"/>
                  </owner>
                </collection>
            """)

        self.checker.requests = []
        self.checker.set_request_ids_search_review()
        self.checker.check_requests()

        self.assertTrue(result['devel_review_added'])

    def test_non_maintainer_double_review(self):

        httpretty.register_uri(httpretty.GET,
            rr("/search/request?withhistory=1&match=state%2F%40name%3D%27review%27+and+review%5B%40by_user%3D%27maintbot%27+and+%40state%3D%27new%27%5D"),
            match_querystring = True,
            body = """
                <collection matches="1">
                  <request id="261355" creator="brassh">
                    <action type="maintenance_incident">
                      <source project="home:brassh" package="mysql-workbench" rev="857c77d2ba1d347b6dc50a1e5bcb74e1"/>
                      <target project="openSUSE:Maintenance" releaseproject="openSUSE:13.2:Update"/>
                    </action>
                    <state name="review" who="lnussel_factory" when="2014-11-13T10:46:52">
                      <comment></comment>
                    </state>
                    <review state="new" by_user="maintbot">
                      <comment></comment>
                    </review>
                    <review state="new" by_package="mysql-workbench" by_project="server:database">
                      <comment>review by devel project</comment>
                    </review>
                    <history who="brassh" when="2014-11-13T09:18:19">
                      <description>Request created</description>
                      <comment>...</comment>
                    </history>
                    <history who="lnussel_factory" when="2014-11-13T10:46:52">
                      <description>Request got a new review request</description>
                    </history>
                    <description>...</description>
                  </request>
                </collection>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/261355",
            match_querystring = True,
            body = """
              <request id="261355" creator="brassh">
                <action type="maintenance_incident">
                  <source project="home:brassh" package="mysql-workbench" rev="857c77d2ba1d347b6dc50a1e5bcb74e1"/>
                  <target project="openSUSE:Maintenance" releaseproject="openSUSE:13.2:Update"/>
                </action>
                <state name="review" who="lnussel_factory" when="2014-11-13T10:46:52">
                  <comment></comment>
                </state>
                <review state="new" by_user="maintbot">
                  <comment></comment>
                </review>
                <review state="new" by_package="mysql-workbench" by_project="server:database">
                  <comment>review by devel project</comment>
                </review>
                <history who="brassh" when="2014-11-13T09:18:19">
                  <description>Request created</description>
                  <comment>...</comment>
                </history>
                <history who="lnussel_factory" when="2014-11-13T10:46:52">
                  <description>Request got a new review request</description>
                </history>
                <description>...</description>
              </request>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/source/home:brassh/mysql-workbench",
            match_querystring = True,
            body = """
                <directory name="mysql-workbench" rev="6" vrev="6" srcmd5="858204decf53f923d5574dbe6ae63b15">
                  <linkinfo project="openSUSE:13.2" package="mysql-workbench" srcmd5="ed9c3b12388cbd14868eb3faabe34685" baserev="ed9c3b12388cbd14868eb3faabe34685" xsrcmd5="08bfb4f40cb1e2de8f9cd4633bf02eb1" lsrcmd5="858204decf53f923d5574dbe6ae63b15" />
                  <serviceinfo code="succeeded" xsrcmd5="6ec4305a8e5363e26a7f4895a0ae12d2" />
                  <entry name="_link" md5="85ef5fb38ca1ec7c300311fda9f4b3d1" size="121" mtime="1414567341" />
                  <entry name="mysql-workbench-community-6.1.7-src.tar.gz" md5="ac059e239869fb77bf5d7a1f5845a8af" size="24750696" mtime="1404405925" />
                  <entry name="mysql-workbench-ctemplate.patch" md5="06ccba1f8275cd9408f515828ecede19" size="1322" mtime="1404658323" />
                  <entry name="mysql-workbench-glib.patch" md5="67fd7d8e3503ce0909381bde747c8a1e" size="1785" mtime="1415732509" />
                  <entry name="mysql-workbench-mysql_options4.patch" md5="9c07dfe1b94af95daf3e16bd6a161684" size="910" mtime="1404658324" />
                  <entry name="mysql-workbench-no-check-for-updates.patch" md5="1f0c9514ff8218d361ea46d3031b2b64" size="1139" mtime="1404658324" />
                  <entry name="mysql-workbench.changes" md5="26bc54777e6a261816b72f64c69630e4" size="13354" mtime="1415747835" />
                  <entry name="mysql-workbench.spec" md5="88b562a93f01b842a5798f809e3c8188" size="7489" mtime="1415745943" />
                  <entry name="openSUSE_(Vendor_Package).xml" md5="ab041af98d7748c216e7e5787ec36f65" size="743" mtime="1315923090" />
                  <entry name="patch-desktop-categories.patch" md5="c24b3283573c34a5e072be122388f8e1" size="391" mtime="1376991147" />
                </directory>
            """)

        result = { 'devel_review_added' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'by_package=mysql-workbench&cmd=addreview&by_project=server%3Adatabase':
                result['devel_review_added'] = True
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/261355",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        httpretty.register_uri(httpretty.GET,
            APIURL + "/search/owner?project=openSUSE:13.2:Update&binary=mysql-workbench",
            match_querystring = True,
            body = """
                <collection/>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/search/owner?binary=mysql-workbench",
            match_querystring = True,
            body = """
                <collection>
                  <owner rootproject="openSUSE" project="server:database" package="mysql-workbench">
                    <person name="Gankov" role="maintainer"/>
                    <person name="bruno_friedmann" role="maintainer"/>
                  </owner>
                </collection>
            """)

        self.checker.requests = []
        self.checker.set_request_ids_search_review()
        self.checker.check_requests()

        self.assertFalse(result['devel_review_added'])



    def test_backports_submit(self):

        httpretty.register_uri(httpretty.GET,
            rr("/search/request?withhistory=1&match=state%2F%40name%3D%27review%27+and+review%5B%40by_user%3D%27maintbot%27+and+%40state%3D%27new%27%5D"),
            match_querystring = True,
            body = """
                <collection matches="1">
                    <request id="261411" creator="lnussel">
                      <action type="maintenance_incident">
                        <source project="home:lnussel:branches:openSUSE:Backports:SLE-12" package="plan" rev="71e76daf2c2e9ddb0b9208f54a14f608"/>
                        <target project="openSUSE:Maintenance" releaseproject="openSUSE:Backports:SLE-12"/>
                      </action>
                      <state name="review" who="lnussel" when="2014-11-13T13:22:02">
                        <comment></comment>
                      </state>
                      <review state="new" by_user="maintbot"/>
                      <history who="lnussel" when="2014-11-13T13:22:02">
                        <description>Request created</description>
                        <comment>test update</comment>
                      </history>
                      <description>test update</description>
                    </request>
                </collection>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/261411",
            body = """
                <request id="261411" creator="lnussel">
                  <action type="maintenance_incident">
                    <source project="home:lnussel:branches:openSUSE:Backports:SLE-12" package="plan" rev="71e76daf2c2e9ddb0b9208f54a14f608"/>
                    <target project="openSUSE:Maintenance" releaseproject="openSUSE:Backports:SLE-12"/>
                  </action>
                  <state name="review" who="lnussel" when="2014-11-13T13:22:02">
                    <comment></comment>
                  </state>
                  <review state="new" by_user="maintbot"/>
                  <history who="lnussel" when="2014-11-13T13:22:02">
                    <description>Request created</description>
                    <comment>test update</comment>
                  </history>
                  <description>test update</description>
                </request>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/source/home:lnussel:branches:openSUSE:Backports:SLE-12/plan",
            body = """
                <directory name="plan" rev="1" vrev="1" srcmd5="b4ed19dc30c1b328168bc62a81ec6998">
                  <linkinfo project="home:lnussel:plan" package="plan" srcmd5="7a2353f73b29dba970702053229542a0" baserev="7a2353f73b29dba970702053229542a0" xsrcmd5="71e76daf2c2e9ddb0b9208f54a14f608" lsrcmd5="b4ed19dc30c1b328168bc62a81ec6998" />
                  <entry name="_link" md5="91f81d88456818a18a7332999fb2da18" size="125" mtime="1415807350" />
                  <entry name="plan.spec" md5="b6814215f6d2e8559b43de9a214b2cbd" size="8103" mtime="1413627959" />
                </directory>

            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/search/owner?project=openSUSE:Backports:SLE-12&binary=plan",
            match_querystring = True,
            body = """
                <collection/>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/search/owner?binary=plan",
            match_querystring = True,
            body = """
                <collection/>
            """)

        result = { 'factory_review_added' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'cmd=addreview&by_user=factory-source':
                result['factory_review_added'] = True
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/261411",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        self.checker.requests = []
        self.checker.set_request_ids_search_review()
        self.checker.check_requests()

        self.assertTrue(result['factory_review_added'])

if __name__ == '__main__':
    unittest.main()

# vim: sw=4 et
