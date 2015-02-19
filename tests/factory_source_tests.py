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

from check_source_in_factory import FactorySourceChecker

APIURL = 'https://testhost.example.com'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')


def rr(s):
    return re.compile(re.escape(APIURL + s))


class TestFactorySourceAccept(unittest.TestCase):

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

        self.checker = FactorySourceChecker(apiurl = APIURL, \
                user = 'factory-source', \
                logger = self.logger)

    def test_accept_request(self):

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/770001",
            body = """
                <request id="770001">
                  <action type="submit">
                    <source project="Base:System" package="timezone" rev="481ecbe0dfc63ece3a1f1b5598f7d96c"/>
                    <target project="openSUSE:13.2" package="timezone"/>
                  </action>
                  <state name="new" who="factory-source" when="2014-10-08T12:06:07">
                    <comment>...</comment>
                  </state>
                  <review state="new" by_user="factory-source"/>
                  <description>...</description>
                </request>
            """)

        httpretty.register_uri(httpretty.GET,
            rr("/source/Base:System/timezone?rev=481ecbe0dfc63ece3a1f1b5598f7d96c&view=info"),
            match_querystring = True,
            body = """
                <sourceinfo package="timezone"
                    rev="481ecbe0dfc63ece3a1f1b5598f7d96c"
                    srcmd5="481ecbe0dfc63ece3a1f1b5598f7d96c"
                    verifymd5="67bac34d29d70553239d33aaf92d2fdd">
                  <filename>timezone.spec</filename>
                </sourceinfo>
            """)
        httpretty.register_uri(httpretty.GET,
            rr("/source/openSUSE:Factory/timezone?view=info"),
            match_querystring = True,
            body = """
                <sourceinfo package="timezone"
                    rev="89"
                    vrev="1"
                    srcmd5="a36605617cbeefa8168bf0ccf3058074"
                    verifymd5="a36605617cbeefa8168bf0ccf3058074">
                  <filename>timezone.spec</filename>
                </sourceinfo>
            """)

        httpretty.register_uri(httpretty.GET,
            rr("/source/openSUSE:Factory/timezone/_history?limit=5"),
            match_querystring = True,
            body = """
                <sourceinfo package="timezone"
                    rev="89"
                    vrev="1"
                    srcmd5="a36605617cbeefa8168bf0ccf3058074"
                    verifymd5="a36605617cbeefa8168bf0ccf3058074">
                  <filename>timezone.spec</filename>
                </sourceinfo>
            """)
        httpretty.register_uri(httpretty.GET,
            rr("/search/request?match=%28state%2F%40name%3D%27new%27+or+state%2F%40name%3D%27review%27%29+and+%28action%2Ftarget%2F%40project%3D%27openSUSE%3AFactory%27+or+submit%2Ftarget%2F%40project%3D%27openSUSE%3AFactory%27+or+action%2Fsource%2F%40project%3D%27openSUSE%3AFactory%27+or+submit%2Fsource%2F%40project%3D%27openSUSE%3AFactory%27%29+and+%28action%2Ftarget%2F%40package%3D%27timezone%27+or+submit%2Ftarget%2F%40package%3D%27timezone%27+or+action%2Fsource%2F%40package%3D%27timezone%27+or+submit%2Fsource%2F%40package%3D%27timezone%27%29+and+action%2F%40type%3D%27submit%27"),
            match_querystring = True,
            responses = [
                httpretty.Response( body = """
                    <collection matches="1">
                      <request id="254684">
                        <action type="submit">
                          <source project="Base:System" package="timezone" rev="481ecbe0dfc63ece3a1f1b5598f7d96c"/>
                          <target project="openSUSE:Factory" package="timezone"/>
                        </action>
                        <state name="review" who="factory-auto" when="2014-10-08T11:55:56">
                          <comment>...</comment>
                        </state>
                        <description> ... </description>
                      </request>
                    </collection>
                    """),
                httpretty.Response( body = """
                    <collection matches="1">
                      <request id="254684">
                        <action type="submit">
                          <source project="Base:System" package="timezone" rev="481ecbe0dfc63ece3a1f1b5598f7d96c"/>
                          <target project="openSUSE:Factory" package="timezone"/>
                        </action>
                        <state name="new" who="factory-auto" when="2014-10-08T11:55:56">
                          <comment>...</comment>
                        </state>
                        <description> ... </description>
                      </request>
                    </collection>
                    """)
                ])

        result = { 'status' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'newstate=accepted&cmd=changereviewstate&by_user=factory-source':
                result['status'] = True
            return (200, headers, '<status code="blah"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/770001",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        # first time request is in in review
        self.checker.set_request_ids(['770001'])
        self.checker.check_requests()

        self.assertEqual(result['status'], None)

        # second time request is in state new so we can accept
        self.checker.set_request_ids(['770001'])
        self.checker.check_requests()

        self.assertTrue(result['status'])

    def test_source_not_in_factory(self):

        httpretty.register_uri(httpretty.GET,
            rr("/search/request?match=state/@name='review'+and+review[@by_user='factory-source'+and+@state='new']&withhistory=1"),
            match_querystring = True,
            body = """
                <collection matches="1">
                    <request id="261411">
                      <action type="maintenance_incident">
                        <source project="home:lnussel:branches:openSUSE:Backports:SLE-12" package="plan" rev="71e76daf2c2e9ddb0b9208f54a14f608"/>
                        <target project="openSUSE:Maintenance" releaseproject="openSUSE:Backports:SLE-12"/>
                      </action>
                      <state name="review" who="maintbot" when="2014-11-13T13:22:02">
                        <comment></comment>
                      </state>
                      <review state="accepted" when="2014-11-13T13:22:02" who="maintbot" by_user="maintbot">
                        <comment>accepted</comment>
                        <history who="maintbot" when="2014-11-13T16:43:09">
                          <description>Review got accepted</description>
                          <comment>accepted</comment>
                        </history>
                      </review>
                      <review state="new" by_user="factory-source"/>
                      <history who="lnussel" when="2014-11-13T13:22:02">
                        <description>Request created</description>
                        <comment>test update</comment>
                      </history>
                      <history who="maintbot" when="2014-11-13T16:43:08">
                        <description>Request got a new review request</description>
                      </history>
                      <description>test update</description>
                    </request>
                </collection>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/261411",
            body = """
                <request id="261411">
                  <action type="maintenance_incident">
                    <source project="home:lnussel:branches:openSUSE:Backports:SLE-12" package="plan" rev="71e76daf2c2e9ddb0b9208f54a14f608"/>
                    <target project="openSUSE:Maintenance" releaseproject="openSUSE:Backports:SLE-12"/>
                  </action>
                  <state name="review" who="maintbot" when="2014-11-13T13:22:02">
                    <comment></comment>
                  </state>
                  <review state="accepted" when="2014-11-13T13:22:02" who="maintbot" by_user="maintbot">
                    <comment>accepted</comment>
                    <history who="maintbot" when="2014-11-13T16:43:09">
                      <description>Review got accepted</description>
                      <comment>accepted</comment>
                    </history>
                  </review>
                  <review state="new" by_user="factory-source"/>
                  <history who="lnussel" when="2014-11-13T13:22:02">
                    <description>Request created</description>
                    <comment>test update</comment>
                  </history>
                  <history who="maintbot" when="2014-11-13T16:43:08">
                    <description>Request got a new review request</description>
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
            rr("/source/openSUSE:Factory/plan?view=info"),
            match_querystring = True,
            status = 404,
            body = """
                <status code="unknown_package">
                    <summary>openSUSE:Factory/plan</summary>
                </status>
            """)

        httpretty.register_uri(httpretty.GET,
            rr("/search/request?match=%28state%2F%40name%3D%27new%27+or+state%2F%40name%3D%27review%27%29+and+%28action%2Ftarget%2F%40project%3D%27openSUSE%3AFactory%27+or+submit%2Ftarget%2F%40project%3D%27openSUSE%3AFactory%27+or+action%2Fsource%2F%40project%3D%27openSUSE%3AFactory%27+or+submit%2Fsource%2F%40project%3D%27openSUSE%3AFactory%27%29+and+%28action%2Ftarget%2F%40package%3D%27plan%27+or+submit%2Ftarget%2F%40package%3D%27plan%27+or+action%2Fsource%2F%40package%3D%27plan%27+or+submit%2Fsource%2F%40package%3D%27plan%27%29+and+action%2F%40type%3D%27submit%27"),
            match_querystring = True,
            body = """
                <collection matches="0">
                </collection>
            """)

        result = { 'factory_source_declined' : None }

        def change_request(result, method, uri, headers):
            u = urlparse.urlparse(uri)
            if u.query == 'newstate=declined&cmd=changereviewstate&by_user=factory-source':
                result['factory_source_declined'] = True
            return (200, headers, '<status code="ok"/>')

        httpretty.register_uri(httpretty.POST,
            APIURL + "/request/261411",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        self.checker.requests = []
        self.checker.set_request_ids_search_review()
        self.checker.check_requests()

        self.assertTrue(result['factory_source_declined'])

if __name__ == '__main__':
    unittest.main()

# vim: sw=4 et
