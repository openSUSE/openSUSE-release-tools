import os
import unittest
import logging
import httpretty
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

        self.checker = FactorySourceChecker(apiurl=APIURL,
                                            user='factory-source',
                                            logger=self.logger)
        self.checker.override_allow = False  # Test setup cannot handle.

    def test_accept_request(self):

        httpretty.register_uri(httpretty.GET,
                               APIURL + '/source/openSUSE:Factory/00Meta/lookup.yml',
                               status=404)

        httpretty.register_uri(httpretty.GET,
                               APIURL + "/request/770001",
                               body="""
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
                               match_querystring=True,
                               body="""
                <sourceinfo package="timezone"
                    rev="481ecbe0dfc63ece3a1f1b5598f7d96c"
                    srcmd5="481ecbe0dfc63ece3a1f1b5598f7d96c"
                    verifymd5="67bac34d29d70553239d33aaf92d2fdd">
                  <filename>timezone.spec</filename>
                </sourceinfo>
            """)

        httpretty.register_uri(httpretty.GET,
                               APIURL + "/source/openSUSE:Factory/timezone/_meta",
                               body="""
               <package name="timezone" project="openSUSE:Factory">
                 <title>timezone</title>
                 <description></description>
               </package>
            """)

        httpretty.register_uri(httpretty.GET,
                               APIURL + "/source/Base:System/timezone/_meta",
                               body="""
               <package name="timezone" project="Base:System">
                 <title>timezone</title>
                 <description></description>
               </package>
            """)

        httpretty.register_uri(httpretty.GET,
                               APIURL + "/source/openSUSE:Factory/timezone?view=info",
                               match_querystring=True,
                               body="""
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
                               match_querystring=True,
                               body="""
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
                               responses=[
                                   httpretty.Response(body="""
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
                                   httpretty.Response(body="""
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

        result = {'status': None}

        def change_request(result, method, uri, headers):
            query = parse_qs(urlparse(uri).query)
            if query == {'by_user': ['factory-source'], 'cmd': ['changereviewstate'], 'newstate': ['accepted']}:
                result['status'] = True
            else:
                result['status'] = 'ERROR'
            return (200, headers, '<status code="blah"/>')

        httpretty.register_uri(httpretty.POST,
                               APIURL + "/request/770001",
                               body=lambda method, uri, headers: change_request(result, method, uri, headers))

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
