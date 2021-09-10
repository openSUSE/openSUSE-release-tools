import os
import unittest
import logging
import httpretty
import osc
import re
from osclib.cache import Cache
from . import OBSLocal

from urllib.parse import urlparse, parse_qs
from check_source_in_factory import FactorySourceChecker

APIURL = 'http://testhost.example.com'
FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')

class TestFactorySourceAccept(OBSLocal.TestCase):

    def tearDown(self):
        httpretty.disable()
        httpretty.reset()

    def setUp(self):
        """
        Initialize the configuration
        """
        super().setUp()

        Cache.last_updated[APIURL] = {'__oldest': '2016-12-18T11:49:37Z'}
        httpretty.reset()
        httpretty.enable(allow_net_connect=False)

        logging.basicConfig()
        self.logger = logging.getLogger(__file__)
        self.logger.setLevel(logging.DEBUG)

        self.checker = FactorySourceChecker(apiurl = APIURL,
                user = 'factory-source',
                logger = self.logger)
        self.checker.override_allow = False # Test setup cannot handle.

    def test_accept_request(self):

        httpretty.register_uri(httpretty.GET,
            APIURL + '/source/openSUSE:Factory/00Meta/lookup.yml',
            status = 404)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/request/770001",
            body = """
                <request id="770001" creator="chameleon">
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
            APIURL + "/source/Base:System/timezone?view=info&rev=481ecbe0dfc63ece3a1f1b5598f7d96c",
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
            APIURL + "/source/openSUSE:Factory/timezone/_meta",
            body = """
               <package name="timezone" project="openSUSE:Factory">
                 <title>timezone</title>
                 <description></description>
               </package>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/source/Base:System/timezone/_meta",
            body = """
               <package name="timezone" project="Base:System">
                 <title>timezone</title>
                 <description></description>
               </package>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + "/source/openSUSE:Factory/timezone?view=info",
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
            APIURL + "/source/openSUSE:Factory/timezone/_history?limit=5",
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
            APIURL + '/search/request',
            responses = [
                httpretty.Response( body = """
                    <collection matches="1">
                      <request id="254684" creator="chameleon">
                        <action type="submit">
                          <source project="Base:System" package="timezone" rev="481ecbe0dfc63ece3a1f1b5598f7d96c"/>
                          <target project="openSUSE:Factory" package="timezone"/>
                        </action>
                        <state name="review" who="factory-auto" when="2014-10-08T11:55:56">
                          <comment>...</comment>
                        </state>
                        <review state="new" by_group="opensuse-review-team">
                          <comment/>
                        </review>
                        <description> ... </description>
                      </request>
                    </collection>
                    """),
                httpretty.Response( body = """
                    <collection matches="1">
                      <request id="254684" creator="chameleon">
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

        result = { 'status': None }

        def change_request(result, method, uri, headers):
            query = parse_qs(urlparse(uri).query)
            if query == {'by_user': ['factory-source'], 'cmd': ['changereviewstate'], 'newstate': ['accepted']}:
                result['status'] = True
            else:
                result['status'] = 'ERROR'
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
            APIURL + '/search/request?match=state%2F%40name%3D%27review%27+and+review%5B%40by_user%3D%27factory-source%27+and+%40state%3D%27new%27%5D&withfullhistory=1',
            match_querystring = True,
            body = """
                <collection matches="1">
                    <request id="261411" creator="lnussel">
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
                <request id="261411" creator="lnussel">
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
            APIURL + "/source/openSUSE:Factory/plan/_meta",
            status = 404,
            body = """
                <status code="unknown_package">
                    <summary>openSUSE:Factory/plan</summary>
                </status>
            """)

        httpretty.register_uri(httpretty.GET,
            APIURL + '/source/openSUSE:Factory/00Meta/lookup.yml',
            status = 404)

        httpretty.register_uri(httpretty.GET,
            APIURL + '/search/request',
            body = """
                <collection matches="0">
                </collection>
            """)

        result = { 'factory_source_declined': None }

        def change_request(result, method, uri, headers):
            query = parse_qs(urlparse(uri).query)
            if query == {'by_user': ['factory-source'], 'cmd': ['changereviewstate'], 'newstate': ['declined']}:
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
