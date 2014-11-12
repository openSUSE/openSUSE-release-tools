#!/usr/bin/python

import os
import unittest
import logging
import httpretty
import osc
import re
import urlparse

from check_source_in_factory import Checker

APIURL = 'https://localhost'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')

foo = """
"""

class TestFactorySourceAccept(unittest.TestCase):

    def setUp(self):
        """
        Initialize the configuration
        """

        httpretty.enable()

        oscrc = os.path.join(FIXTURES, 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        osc.conf.config['debug'] = 1

        logging.basicConfig()
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.DEBUG)

        self.checker = Checker(apiurl = APIURL, \
                user = 'test-reviewer', \
                logger = self.logger)

    def test_accept_request(self):

        httpretty.register_uri(httpretty.GET,
            "https://localhost/request/770001",
            body = """
                <request id="770001">
                  <action type="submit">
                    <source project="Base:System" package="timezone" rev="481ecbe0dfc63ece3a1f1b5598f7d96c"/>
                    <target project="openSUSE:13.2" package="timezone"/>
                  </action>
                  <state name="new" who="test-reviewer" when="2014-10-08T12:06:07">
                    <comment>...</comment>
                  </state>
                  <review state="new" by_user="test-reviewer"/>
                  <description>...</description>
                </request>
            """)

        httpretty.register_uri(httpretty.GET,
            "https://localhost/source/Base:System/timezone?rev=481ecbe0dfc63ece3a1f1b5598f7d96c&view=info",
            body = """
                <sourceinfo package="timezone"
                    rev="481ecbe0dfc63ece3a1f1b5598f7d96c"
                    srcmd5="481ecbe0dfc63ece3a1f1b5598f7d96c"
                    verifymd5="67bac34d29d70553239d33aaf92d2fdd">
                  <filename>timezone.spec</filename>
                </sourceinfo>
            """)
        httpretty.register_uri(httpretty.GET,
            "https://localhost/source/openSUSE:Factory/timezone?view=info",
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
            "https://localhost/source/openSUSE:Factory/timezone/_history?limit=5",
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
            "https://localhost/search/request?match=%28state%2F%40name%3D%27new%27+or+state%2F%40name%3D%27review%27%29+and+%28action%2Ftarget%2F%40project%3D%27openSUSE%3AFactory%27+or+submit%2Ftarget%2F%40project%3D%27openSUSE%3AFactory%27+or+action%2Fsource%2F%40project%3D%27openSUSE%3AFactory%27+or+submit%2Fsource%2F%40project%3D%27openSUSE%3AFactory%27%29+and+%28action%2Ftarget%2F%40package%3D%27timezone%27+or+submit%2Ftarget%2F%40package%3D%27timezone%27+or+action%2Fsource%2F%40package%3D%27timezone%27+or+submit%2Fsource%2F%40package%3D%27timezone%27%29+and+action%2F%40type%3D%27submit%27",
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
            print "called"
            u = urlparse.urlparse(uri)
            if u.query == 'newstate=accepted&cmd=changereviewstate&by_user=test-reviewer':
                result['status'] = True
            return (200, headers, '<status code="blah"/>')

        httpretty.register_uri(httpretty.POST,
            "https://localhost/request/770001",
            body = lambda method, uri, headers: change_request(result, method, uri, headers))

        # first time request is in in review
        self.checker.set_request_ids(['770001'])
        self.checker.check_requests()

        self.assertEqual(result['status'], None)

        # second time request is in state new so we can accept
        self.checker.set_request_ids(['770001'])
        self.checker.check_requests()

        self.assertTrue(result['status'])

if __name__ == '__main__':
    unittest.main()

# vim: sw=4 et
