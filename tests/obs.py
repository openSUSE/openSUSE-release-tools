import os
import re
import string
import time
import urlparse
import xml.etree.cElementTree as ET

import httpretty
import osc
import osc.core


APIURL = 'https://localhost'

FIXTURES = os.path.join(os.getcwd(), 'tests/fixtures')

DEBUG = True


# The idiotic routing system of httpretty use a hash table.  Because
# we have a default() handler, we need a deterministic routing
# mechanism.
_table = {
    httpretty.GET: [],
    httpretty.POST: [],
    httpretty.PUT: [],
    httpretty.DELETE: [],
}


def router_handler(route_table, method, request, uri, headers):
    """Route the URLs in a deterministic way."""
    uri_parsed = urlparse.urlparse(uri)
    for path, fn in route_table:
        match = False
        if isinstance(path, basestring) and uri_parsed.path == path:
            match = True
        elif not isinstance(path, basestring) and path.search(uri_parsed.path):
            match = True
        if match:
            return fn(request, uri, headers)
    raise Exception('Not found entry for method %s for url %s' % (method, uri))


def router_handler_GET(request, uri, headers):
    return router_handler(_table[httpretty.GET], 'GET', request, uri, headers)


def router_handler_POST(request, uri, headers):
    return router_handler(_table[httpretty.POST], 'POST', request, uri, headers)


def router_handler_PUT(request, uri, headers):
    return router_handler(_table[httpretty.PUT], 'PUT', request, uri, headers)


def router_handler_DELETE(request, uri, headers):
    return router_handler(_table[httpretty.DELETE], 'DELETE', request, uri, headers)


httpretty.register_uri(httpretty.GET, re.compile(r'.*'), body=router_handler_GET)
httpretty.register_uri(httpretty.POST, re.compile(r'.*'), body=router_handler_POST)
httpretty.register_uri(httpretty.PUT, re.compile(r'.*'), body=router_handler_PUT)
httpretty.register_uri(httpretty.DELETE, re.compile(r'.*'), body=router_handler_DELETE)


def method_decorator(method, path):
    def _decorator(fn):
        def _fn(*args, **kwargs):
            return fn(OBS._self, *args, **kwargs)
        _table[method].append((path, _fn))
        return _fn
    return _decorator


def GET(path):
    return method_decorator(httpretty.GET, path)


def POST(path):
    return method_decorator(httpretty.POST, path)


def PUT(path):
    return method_decorator(httpretty.PUT, path)


def DELETE(path):
    return method_decorator(httpretty.DELETE, path)


class OBS(object):
    # This class will become a singleton
    _self = None

    def __new__(cls, *args, **kwargs):
        """Class constructor."""
        if not OBS._self:
            OBS._self = super(OBS, cls).__new__(cls, *args, **kwargs)
        return OBS._self

    def __init__(self, fixtures=FIXTURES):
        """Instance constructor."""
        self.fixtures = fixtures

        httpretty.enable()

        oscrc = os.path.join(fixtures, 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        os.environ['OSC_CONFIG'] = oscrc

        # Internal status of OBS.  The mockup will use this data to
        # build the responses.  We will try to put responses as XML
        # templates in the fixture directory.
        self.requests = {
            '123': {
                'request': 'new',
                'review': 'accepted',
                'who': 'Admin',
                'by': 'group',
                'id': '123',
                'by_who': 'opensuse-review-team',
                'package': 'gcc',
            },
            '321': {
                'request': 'review',
                'review': 'new',
                'who': 'Admin',
                'by': 'group',
                'id': '321',
                'by_who': 'factory-staging',
                'package': 'puppet',
            },
            '333': {
                'request': 'review',
                'review': 'new',
                'who': 'Admin',
                'by': 'project',
                'id': '333',
                'by_who': 'openSUSE:Factory:Staging:B',
                'package': 'wine',
            },
            '501': {
                'request': 'review',
                'review': 'new',
                'who': 'Admin',
                'by': 'project',
                'id': '501',
                'by_who': 'openSUSE:Factory:Staging:C',
                'package': 'apparmor',
            },
            '502': {
                'request': 'review',
                'review': 'new',
                'who': 'Admin',
                'by': 'project',
                'id': '502',
                'by_who': 'openSUSE:Factory:Staging:C',
                'package': 'mariadb',
            },
            '1000': {
                'request': 'review',
                'review': 'new',
                'who': 'Admin',
                'by': 'user',
                'id': '1000',
                'by_who': 'factory-repo-checker',
                'package': 'emacs',
            },
            '1001': {
                'request': 'review',
                'review': 'new',
                'who': 'Admin',
                'by': 'user',
                'id': '1001',
                'by_who': 'factory-repo-checker',
                'package': 'python',
            },
        }

        self.staging_project = {
            'A': {
                'project': 'openSUSE:Factory:Staging:A',
                'title': '',
                'description': '',
            },
            'U': {
                'project': 'openSUSE:Factory:Staging:U',
                'title': 'Unfrozen',
                'description': '',
            },
            'B': {
                'project': 'openSUSE:Factory:Staging:B',
                'title': 'wine',
                'description': 'requests:\n- {id: 333, package: wine}',
            },
            'C': {
                'project': 'openSUSE:Factory:Staging:C',
                'title': 'A project ready to be accepted',
                'description': ('requests:\n- {id: 501, package: apparmor, author: Admin}\n'
                                '- {id: 502, package: mariadb, author: Admin}'),
            },
            'J': {
                'project': 'openSUSE:Factory:Staging:J',
                'title': 'A project to be checked',
                'description': ('requests:\n- {id: 1000, package: emacs, author: Admin}\n'
                                '- {id: 1001, package: python, author: Admin}'),
            },
        }

        self.links = {
            'openSUSE:Factory:Staging:B/wine': {
                'prj': 'openSUSE:Factory:Staging:B',
                'pkg': 'wine',
                'devprj': 'home:Admin',
            },
            'openSUSE:Factory:Staging:C/apparmor': {
                'prj': 'openSUSE:Factory:Staging:C',
                'pkg': 'apparmor',
                'devprj': 'home:Admin',
            },
            'openSUSE:Factory:Staging:C/mariadb': {
                'prj': 'openSUSE:Factory:Staging:C',
                'pkg': 'mariadb',
                'devprj': 'home:Admin',
            },
            'openSUSE:Factory:Staging:J/emacs': {
                'prj': 'openSUSE:Factory:Staging:J',
                'pkg': 'emacs',
                'devprj': 'home:Admin',
            },
            'openSUSE:Factory:Staging:J/python': {
                'prj': 'openSUSE:Factory:Staging:J',
                'pkg': 'python',
                'devprj': 'home:Admin',
            },
        }

        self.meta = {}

        self.package = {
            'home:Admin/gcc': {
                'rev': '1',
                'vrev': '1',
                'name': 'gcc',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'home:Admin/wine': {
                'rev': '1',
                'vrev': '1',
                'name': 'wine',
                'srcmd5': 'de9a9f5e3bedb01980465f3be3d236cb',
            },
            'home:Admin/puppet': {
                'rev': '1',
                'vrev': '1',
                'name': 'puppet',
                'srcmd5': 'de8a9f5e3bedb01980465f3be3d236cb',
            },
            'openSUSE:Factory/gcc': {
                'rev': '1',
                'vrev': '1',
                'name': 'gcc',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'openSUSE:Factory/wine': {
                'rev': '1',
                'vrev': '1',
                'name': 'wine',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'openSUSE:Factory:Rings:0-Bootstrap/elem-ring0': {
                'rev': '1',
                'vrev': '1',
                'name': 'elem-ring0',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'openSUSE:Factory/binutils': {
                'rev': '1',
                'vrev': '1',
                'name': 'wine',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'home:Admin/apparmor': {
                'rev': '1',
                'vrev': '1',
                'name': 'apparmor',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'openSUSE:Factory/apparmor': {
                'rev': '1',
                'vrev': '1',
                'name': 'apparmor',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'home:Admin/mariadb': {
                'rev': '1',
                'vrev': '1',
                'name': 'mariadb',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'openSUSE:Factory/mariadb': {
                'rev': '1',
                'vrev': '1',
                'name': 'mariadb',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'home:Admin/emacs': {
                'rev': '1',
                'vrev': '1',
                'name': 'emacs',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
                'lsrcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
                'verifymd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
            'home:Admin/python': {
                'rev': '1',
                'vrev': '1',
                'name': 'python',
                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
                'lsrcmd5': 'de7a9f5e3bedb01980465f3be3d236cb',
                'verifymd5': 'de7a9f5e3bedb01980465f3be3d236cb',
            },
        }

        self.comments = {
            'openSUSE:Factory:Staging:A': [
                {
                    'who': 'Admin',
                    'when': '2014-06-01 17:56:28 UTC',
                    'id': '1',
                    'body': 'Just a comment',
                }
            ],
            'openSUSE:Factory:Staging:U': [],
            'openSUSE:Factory:Staging:B': [],
            'openSUSE:Factory:Staging:C': [
                {
                    'who': 'Admin',
                    'when': '2014-06-01 17:56:28 UTC',
                    'id': '2',
                    'body': ("The list of requests tracked in openSUSE:Factory:Staging:C has changed:\n\n"
                             " * Request#501 for package apparmor submitted by Admin\n"
                             " * Request#502 for package mariadb submitted by Admin\n")
                }
            ],
            'openSUSE:Factory:Staging:J': [],
        }

        # To track comments created during test execution, even if they have
        # been deleted afterward
        self.comment_bodies = []

    #
    #  /request/
    #

    @GET(re.compile(r'/request/\d+'))
    def request(self, request, uri, headers):
        """Return a request XML description."""
        request_id = re.search(r'(\d+)', uri).group(1)
        response = (404, headers, '<result>Not found</result>')
        try:
            template = string.Template(self._fixture(uri))
            response = (200, headers, template.substitute(self.requests[request_id] if request_id in self.requests else dict()))
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'REQUEST', uri, response

        return response

    def _request(self, request_id):
        """Utility function to recover a request from the ID."""
        template = string.Template(self._fixture(urlparse.urljoin(APIURL, '/request/' + request_id)))
        return template.substitute(self.requests[request_id])

    @POST(re.compile(r'/request/\d+'))
    def review_request(self, request, uri, headers):
        request_id = re.search(r'(\d+)', uri).group(1)
        qs = urlparse.parse_qs(urlparse.urlparse(uri).query)

        response = (404, headers, '<result>Not found</result>')

        # Adding review
        if qs.get('cmd', None) == ['addreview']:
            self.requests[request_id]['request'] = 'review'
            self.requests[request_id]['review'] = 'new'
        # Changing review
        if qs.get('cmd', None) == ['changereviewstate']:
            self.requests[request_id]['request'] = 'new'
            self.requests[request_id]['review'] = qs['newstate'][0]
        # Project review
        if 'by_project' in qs:
            self.requests[request_id]['by'] = 'project'
            self.requests[request_id]['by_who'] = qs['by_project'][0]
        # Group review
        if 'by_group' in qs:
            self.requests[request_id]['by'] = 'group'
            self.requests[request_id]['by_who'] = qs[u'by_group'][0]

        try:
            response = (200, headers, self._request(request_id))
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'REVIEW REQUEST', uri, response

        return response

    @GET('/request')
    def request_search(self, request, uri, headers):
        """Request search function."""
        qs = urlparse.parse_qs(urlparse.urlparse(uri).query)
        states = qs['states'][0].split(',')

        response = (404, headers, '<result>Not found</result>')

        requests = [rq for rq in self.requests.values() if rq['request'] in states]
        if 'package' in qs:
            requests = [rq for rq in requests if qs['package'][0] in rq['package']]

        try:
            _requests = '\n'.join(self._request(rq['id']) for rq in requests)

            template = string.Template(self._fixture(uri, filename='result.xml'))
            result = template.substitute(
                {
                    'nrequests': len(requests),
                    'requests': _requests,
                })
            response = (200, headers, result)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SEARCH REQUEST', uri, response

        return response

    #
    # /source/
    #

    @GET(re.compile(r'/source/openSUSE:Factory:Staging:[A|B|C|J]/_project'))
    def source_staging_project_project(self, request, uri, headers):
        """Return the _project information for a staging project."""
        # Load the proper fixture and adjust mtime according the
        # current time.
        response = (404, headers, '<result>Not found</result>')
        try:
            template = string.Template(self._fixture(uri))
            if 'Staging:A' in uri:
                project = template.substitute({'mtime': int(time.time()) - 3600 * 24 * 356})
            else:
                project = template.substitute({'mtime': int(time.time()) - 100})
            response = (200, headers, project)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'STAGING PROJECT _PROJECT', uri, response

        return response

    @GET(re.compile(r'/source/openSUSE:Factory:Staging:[A|B|C|U|J](/\w+)?/_meta'))
    def source_staging_project_meta(self, request, uri, headers):
        """Return the _meta information for a staging project."""
        key = re.search(r'openSUSE:Factory:Staging:(\w(?:/\w+)?)/_meta', uri).group(1)

        response = (404, headers, '<result>Not found</result>')
        try:
            if key not in self.meta:
                template = string.Template(self._fixture(uri))
                self.meta[key] = template.substitute(self.staging_project[key])

            meta = self.meta[key]
            response = (200, headers, meta)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'STAGING PROJECT [PACKAGE] _META', uri, response

        return response

    @PUT(re.compile(r'/source/openSUSE:Factory:Staging:[A|B|C|U|J](/\w+)?/_meta'))
    def put_source_staging_project_meta(self, request, uri, headers):
        """Set the _meta information for a staging project."""
        key = re.search(r'openSUSE:Factory:Staging:(\w(?:/\w+)?)/_meta', uri).group(1)

        self.meta[key] = request.body

        meta = self.meta[key]
        response = (200, headers, meta)

        if DEBUG:
            print 'PUT STAGING PROJECT [PACKAGE] _META', uri, response

        return response

    @GET(re.compile(r'/source/openSUSE:Factory:Staging:B/wine'))
    def source_stating_project_wine(self, request, uri, headers):
        """Return wine package information. Is a link."""
        package = re.search(r'/source/([\w:]+/\w+)', uri).group(1)
        response = (404, headers, '<result>Not found</result>')
        try:
            template = string.Template(self._fixture(uri))
            response = (200, headers, template.substitute(self.links[package]))
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SOURCE WINE', uri, response

        return response

    @GET(re.compile(r'/source/(?:Base:System|openSUSE:Factory)/timezone'))
    def source_base_system_timezone(self, request, uri, headers):
        """Return timezone package information. Is a link."""
        response = (404, headers, '<result>Not found</result>')
        u = urlparse.urlparse(uri)
        if (u.path.startswith('/source/Base:System')):
            if u.query == 'rev=481ecbe0dfc63ece3a1f1b5598f7d96c&view=info':
                response = (200, headers, """
                    <sourceinfo package="timezone"
                        rev="481ecbe0dfc63ece3a1f1b5598f7d96c"
                        srcmd5="481ecbe0dfc63ece3a1f1b5598f7d96c"
                        verifymd5="67bac34d29d70553239d33aaf92d2fdd">
                      <filename>timezone.spec</filename>
                    </sourceinfo>
                    """)
            else:
                print 'REQUEST', uri
                assert False
        elif (u.path.startswith('/source/openSUSE:Factory')):
            if u.path.endswith('/_history'):
                    response = (200, headers, """
                        <revisionlist>
                          <revision rev="85" vrev="1">
                            <srcmd5>8fbd89c09bb7611e4da38a64d7c7ec7f</srcmd5>
                            <version>2014c</version>
                            <time>1400783885</time>
                            <user>coolo</user>
                            <comment>Update to 2014c (bnc#877535) (forwarded request 233760 from leonardocf)</comment>
                            <requestid>233811</requestid>
                          </revision>
                        </revisionlist>
                    """)
            else:
                if u.query == 'view=info':
                    response = (200, headers, """
                        <sourceinfo package="timezone"
                            rev="89"
                            vrev="1"
                            srcmd5="a36605617cbeefa8168bf0ccf3058074"
                            verifymd5="a36605617cbeefa8168bf0ccf3058074">
                          <filename>timezone.spec</filename>
                        </sourceinfo>
                        """)
                else:
                    print 'REQUEST', uri
                    assert False

        if DEBUG:
            print 'REQUEST', uri, response

        return response


    @DELETE(re.compile('/source/openSUSE:Factory:Staging:[B|C]/\w+'))
    def delete_package(self, request, uri, headers):
        """Delete a source package from a Staging project."""
        package = re.search(r'/source/([\w:]+/\w+)', uri).group(1)
        response = (404, headers, '<result>Not found</result>')
        try:
            del self.links[package]
            response = (200, headers, '<result>Ok</result>')
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'DELETE', uri, response

        return response

    @GET(re.compile(r'/source/home:Admin/\w+'))
    def source_project(self, request, uri, headers):
        """Return information of a source package."""
        qs = urlparse.parse_qs(urlparse.urlparse(uri).query)
        index = re.search(r'/source/([\w:]+/\w+)', uri).group(1)
        project, package = index.split('/')
        response = (404, headers, '<result>Not found</result>')

        suffix = '_expanded' if 'expanded' in qs else '_info' if 'info' in qs else ''
        path = os.path.join('source', project, package + suffix)

        try:
            template = string.Template(self._fixture(path=path))
            response = (200, headers, template.substitute(self.package[index]))
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SOURCE HOME:ADMIN', package, uri, response

        return response

    @POST(re.compile(r'/source/openSUSE:Factory:Rings:1-MinimalX/\w+'))
    def show_wine_link(self, request, uri, headers):
        # TODO: only useful answer if cmd=showlinked
        return (200, headers, '<collection/>')

    @GET('/source/openSUSE:Factory:Staging:A/wine')
    def source_link(self, request, uri, headers):
        project_package = re.search(r'/source/([\w:]+/\w+)', uri).group(1)
        response = (404, headers, '<result>Not found</result>')
        try:
            template = string.Template(self._fixture(uri))
            response = (200, headers, template.substitute(self.links[project_package]))
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SOURCE HOME:ADMIN WINE', uri, response

        return response

    @PUT(re.compile(r'/source/openSUSE:Factory:Staging:[AB]/\w+/_link'))
    def put_source_link(self, request, uri, headers):
        """Create wine link in staging project A."""
        project_package = re.search(r'/source/([\w:]+/\w+)/_link', uri).group(1)
        project, package = re.search(r'([\w:]+)/(\w+)', project_package).groups()
        response = (404, headers, '<result>Not found</result>')
        try:
            _link = ET.fromstring(request.body)
            self.links[project_package] = {
                'prj': project,
                'pkg': package,
                'devprj': _link.get('project')
            }
            response = (200, headers, '<result>Ok</result>')

        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'PUT SOURCE WINE _LINK', uri, response

        return response

    #
    #  /build/
    #

    # @GET(re.compile(r'build/home:Admin/_result'))
    # def build_lastsuccess(self, request, uri, headers):
    #     package = re.search(r'/source/([\w:]+/\w+)', uri).group(1)
    #     response = (404, headers, '<result>Not found</result>')
    #     try:
    #         template = string.Template(self._fixture(uri))
    #         response = (200, headers, template.substitute(self.package[package]))
    #     except Exception as e:
    #         if DEBUG:
    #             print uri, e

    #     if DEBUG:
    #         print 'BUILD _RESULT LASTBUILDSUCCESS', package, uri, response

    #     return response

    #
    #  /search/
    #

    @GET('/search/project/id')
    def search_project_id(self, request, uri, headers):
        """Return a search result /search/project/id."""
        assert urlparse.urlparse(uri).query == "match=starts-with(@name,'openSUSE:Factory:Staging:')"

        response = (404, headers, '<result>Not found</result>')
        try:
            template = string.Template(self._fixture(uri, filename='result.xml'))
            projects = '\n'.join(
                '<project name="%s"/>' % staging['project'] for staging in self.staging_project.values()
            )
            result = template.substitute(
                {
                    'nprojects': len(self.staging_project),
                    'projects': projects,
                })
            response = (200, headers, result)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SEARCH PROJECT ID', uri, response

        return response

    @GET('/search/request')
    def search_request(self, request, uri, headers):
        """Return a search result for /search/request."""
        query = urlparse.urlparse(uri).query
        assert query in (
            "match=state/@name='review'+and+review[@by_group='factory-staging'+and+@state='new']+and+(target[@project='openSUSE:Factory']+or+target[@project='openSUSE:Factory:NonFree'])",
            "match=state/@name='review'+and+review[@by_user='factory-repo-checker'+and+@state='new']+and+(target[@project='openSUSE:Factory']+or+target[@project='openSUSE:Factory:NonFree'])"
        )

        response = (404, headers, '<result>Not found</result>')

        by, by_who = re.search(r"@by_(user|group)='([-\w]+)'", query).groups()
        state = re.search(r"@state='(\w+)'", query).group(1)

        requests = [rq for rq in self.requests.values()
                    if rq['request'] == 'review'
                    and rq['review'] == state
                    and rq['by'] == by
                    and rq['by_who'] == by_who]

        try:
            _requests = '\n'.join(self._request(rq['id']) for rq in requests)

            template = string.Template(self._fixture(uri, filename='result.xml'))
            result = template.substitute(
                {
                    'nrequests': len(requests),
                    'requests': _requests,
                })
            response = (200, headers, result)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SEARCH REQUEST', uri, response

        return response

    @GET('/search/request/id')
    def search_request_id(self, request, uri, headers):
        """Return a search result for /search/request/id."""
        query = urlparse.urlparse(uri).query
        project = re.search(r"@by_project='([^']+)'", query).group(1)

        response = (404, headers, '<result>Not found</result>')

        requests = [rq for rq in self.requests.values()
                    if rq['request'] == 'review'
                    and rq['review'] == 'new'
                    and rq['by'] == 'project'
                    and rq['by_who'] == project]

        try:
            _requests = '\n'.join('<request id="%s"/>' % rq['id'] for rq in requests)

            template = string.Template(self._fixture(uri, filename='result.xml'))
            result = template.substitute(
                {
                    'nrequests': len(requests),
                    'requests': _requests,
                })
            response = (200, headers, result)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'SEARCH REQUEST', uri, response

        return response

    #
    #  /comments/
    #

    @GET(re.compile(r'/comments/project/.*'))
    def get_comment(self, request, uri, headers):
        """Get comments for a project."""
        prj = re.search(r'comments/project/([^/]*)', uri).group(1)
        comments = self.comments[prj]
        if not comments:
            return (200, headers, '<comments project="%s"/>' % prj)
        else:
            ret_str = '<comments project="%s">' % prj
            for c in comments:
                ret_str += '<comment who="%s" when="%s" id="%s">' % (c['who'], c['when'], c['id'])
                ret_str += c['body'].replace('<', '&lt;').replace('>', '&gt;')
                ret_str += '</comment>'
            ret_str += '</comments>'
            return (200, headers, ret_str)

    @POST(re.compile(r'/comments/project/.*'))
    def post_comment(self, request, uri, headers):
        """Add comment to a project."""
        prj = re.search(r'comments/project/([^/]*)', uri).group(1)
        comment = {
            'who': 'Admin',
            'when': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
            'id': str(sum(len(c) for c in self.comments.values()) + 1),
            'body': request.body
        }
        self.comments[prj].append(comment)
        self.comment_bodies.append(request.body)
        response = (200, headers, '<result>Ok</result>')
        return response

    @DELETE(re.compile(r'/comment/\d+'))
    def delete_comment(self, request, uri, headers):
        """Delete a comments."""
        comment_id = re.search(r'comment/(\d+)', uri).group(1)
        for prj in self.comments:
            self.comments[prj] = [c for c in self.comments[prj] if c['id'] != comment_id]
        return (200, headers, '<result>Ok</result>')

    #
    # /project/staging_projects
    #

    @GET(re.compile(r'/project/staging_projects/openSUSE:Factory.*'))
    def staging_projects(self, request, uri, headers):
        """Return a JSON fixture."""
        response = (404, headers, '<result>Not found</result>')
        try:
            path = urlparse.urlparse(uri).path + '.json'
            fixture = self._fixture(path=path)
            response = (200, headers, fixture)
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'REQUEST', uri, response

        return response

    #
    #  Static fixtures
    #

    @GET(re.compile(r'.*'))
    def default(self, request, uri, headers):
        """Default handler. Search in the fixture directory."""
        response = (404, headers, '<result>Not found</result>')
        try:
            response = (200, headers, self._fixture(uri))
        except Exception as e:
            if DEBUG:
                print uri, e

        if DEBUG:
            print 'DEFAULT', uri, response

        return response

    def _fixture(self, uri=None, path=None, filename=None):
        """Read a file as a fixture."""
        if not path:
            path = urlparse.urlparse(uri).path
        path = path[1:] if path.startswith('/') else path

        if filename:
            fixture_path = os.path.join(self.fixtures, path, filename)
        else:
            fixture_path = os.path.join(self.fixtures, path)

        with open(fixture_path) as f:
            return f.read()
