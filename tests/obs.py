#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or later

import os
import sys
import httpretty
import xml.etree.ElementTree as ET
import time

from string import Template
import oscs
import osc
import re
import pprint
import posixpath


PY3 = sys.version_info[0] == 3

if PY3:
    string_types = str,
else:
    string_types = basestring,


class OBS(object):
    """
    Class trying to simulate a simple OBS
    """

    def __init__(self):
        """
        Initialize the configuration and create basic OBS instance
        """

        # Make osc happy about config file
        oscrc = os.path.join(self._get_fixtures_dir(), 'oscrc')
        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        os.environ['OSC_CONFIG'] = oscrc

        # (Re)set configuration
        self.reset_config()

    def reset_config(self):
        """
        Resets whole OBS class
        """
        # Initialize states
        self._set_init_data()
        # Setup callbacks
        self._clear_responses()

    def _set_init_data(self):
        """
        Resets states
        """
        # XXX TODO Write fixtures in an external file, or recreate
        # this data from other fixtures
        # Initial request data
        self.requests_data = {
            '123': {'request': 'new', 'review': 'accepted', 'who': 'Admin', 'by': 'group',
                    'id': '123', 'by_who': 'opensuse-review-team', 'package': 'gcc'},
            '321': {'request': 'review', 'review': 'new', 'who': 'Admin', 'by': 'group',
                    'id': '321', 'by_who': 'factory-staging', 'package': 'puppet'},
            '333': {'request': 'review', 'review': 'new', 'who': 'Admin', 'by': 'project',
                    'id': '333', 'by_who': 'openSUSE:Factory:Staging:B', 'package': 'wine'}
        }
        self.st_project_data = {
            'A': {'project': 'openSUSE:Factory:Staging:A', 'title': '', 'description': ''},
            'U': {'project': 'openSUSE:Factory:Staging:U', 'title': 'Unfrozen', 'description': ''},
            'B': {'project': 'openSUSE:Factory:Staging:B', 'title': 'wine',
                  'description': 'requests:\n- {id: 333, package: wine}'}
        }
        self.links_data = {
            'openSUSE:Factory:Staging:B/wine': {
                'prj': 'openSUSE:Factory:Staging:B', 'pkg': 'wine', 'devprj': 'home:Admin'
            }
        }
        self.pkg_data = {
            'home:Admin/gcc': {'rev': '1', 'vrev': '1', 'name': 'gcc',
                               'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb'},
            'home:Admin/wine': {'rev': '1', 'vrev': '1', 'name': 'wine',
                                'srcmd5': 'de7a9f5e3bedb01980465f3be3d236cb'}
        }

    def _clear_responses(self):
        """
        Resets predefined responses
        """
        self.responses = {'DELETE': {}, 'GET': {}, 'PUT': {}, 'POST': {}, 'ALL': {}}

        # Add methods to manipulate reviews
        self._request_review()
        # Add methods to search requests and projects
        self._search()
        # Add methods to work with project metadata
        self._project_meta()
        # Add linked packages
        self._link_sources()
        # Add packages
        self._pkg_sources()
        # Build results
        self._build_results()
        # Workaround
        self._ugly_hack()

    def _pretty_callback(self, request, uri, headers, exception=True):
        """
        Custom callback for HTTPretty.

        It mocks requests and replaces calls with either xml, content of file,
        function call or first item in array of those.

        :param request: request as provided to callback function by HTTPretty
        :param uri: uri as provided to callback function by HTTPretty
        :param headers: headers as provided to callback function by HTTPretty
        """

        # Get path
        path = re.match(r'.*localhost([^?]*)(\?.*)?', uri).group(1)
        reply = None
        # Try to find a fallback
        if path in self.responses['ALL']:
            reply = self.responses['ALL'][path]
        # Try to find a specific method
        if path in self.responses[request.method]:
            reply = self.responses[request.method][path]
        # We have something to reply with
        if reply:
            # It's a dict, therefore there is return code as well
            if isinstance(reply, dict):
                ret_code = reply['status']
                reply = reply['reply']
            else:
                ret_code = 200
            # It's a list, so take the first
            if isinstance(reply, list):
                reply = reply.pop(0)
            # It's string
            if isinstance(reply, string_types):
                # It's XML
                if reply.startswith('<'):
                    return (ret_code, headers, reply)
                # It's fixture
                else:
                    return (ret_code, headers, self._get_fixture_content(reply))
            # All is left is callback function
            else:
                return (ret_code, headers, reply(self.responses, request, uri))
        # No possible response found
        else:
            if len(path) == 0:
                path = uri
            if len(path) > 1:
                # XXX Warning. This ret is not returned
                ret = self._pretty_callback(request, 'https://localhost' + posixpath.dirname(path), headers, False)
            if exception:
                raise BaseException("No tests/obs.pyresponse for {} on {} provided".format(request.method, uri))
            else:
                return None

    def _ugly_hack(self):
        """
        Static fixtures we don't have a way of generating yet

        Whole point of this setup is to cleanup all tests and be able to move
        everything to new test-suite.
        """
        # Build results verification, maybe not worth of dynamic processing
        self.responses['GET']['/build/red/_result'] = 'build-results-red.xml'
        self.responses['GET']['/build/green/_result'] = 'build-results-green.xml'

        # Testing of rings
        self.responses['GET']['/source/openSUSE:Factory:Rings:0-Bootstrap'] = 'ring-0-project.xml'
        self.responses['GET']['/source/openSUSE:Factory:Rings:1-MinimalX'] = 'ring-1-project.xml'

        # Testing of frozen packages
        tmpl = Template(self._get_fixture_content('project-f-metalist.xml'))
        self.responses['GET']['/source/openSUSE:Factory:Staging:B/_project'] = tmpl.substitute({'mtime': int(time.time()) - 100})
        self.responses['GET']['/source/openSUSE:Factory:Staging:A/_project'] = tmpl.substitute({'mtime': int(time.time()) - 3600*24*356})
        self.responses['GET']['/source/openSUSE:Factory:Staging:U/_project'] = 'project-u-metalist.xml'

    def _build_results(self):
        """
        Mimic build results, B is broken, A works
        """

        def build_results(responses, request, uri):
            ret_str = '<resultlist state="c7856c90c70c53fae88aacec964b80c0">\n'
            prj = re.match(r'.*/([^/]*)/_result', uri).group(1)
            if prj == 'openSUSE:Factory:Staging:B':
                states = ['failed', 'broken', 'building']
            else:
                states = ['excluded', 'succeeded']
            for st in states:
                if st == 'building':
                    ret_str += '  <result project="{0}" repository="{1}" arch="x86_64" code="{2}" state="{2}">\n'.format(prj, st, st)
                else:
                    ret_str += '  <result project="{0}" repository="{1}" arch="x86_64" code="{2}" state="{2}">\n'.format(prj, st, "published")
                for dt in self.links_data:
                    if self.links_data[dt]['prj'] == prj:
                        ret_str += '    <status package="{}" code="{}" />\n'.format(self.links_data[dt]['pkg'], st)
                ret_str += '  </result>\n'
            ret_str += '</resultlist>\n'
            return ret_str

        self.responses['GET']['/build/openSUSE:Factory:Staging:A/_result'] = build_results
        self.responses['GET']['/build/openSUSE:Factory:Staging:B/_result'] = build_results

    def _project_meta(self):
        # Load template
        tmpl = Template(self._get_fixture_content('staging-project-meta.xml'))

        def project_meta_change(responses, request, uri):
            path = re.match(r'.*localhost([^?]*)(\?.*)?', uri).group(1)
            self.responses['GET'][path] = request.body
            return self.responses['GET'][path]

        # Register methods for all requests
        for pr in self.st_project_data:
            # Static response for gets (just filling template from local data)
            self.responses['GET']['/source/openSUSE:Factory:Staging:' + pr + '/_meta'] = tmpl.substitute(self.st_project_data[pr])
            # Interpret other requests
            self.responses['ALL']['/source/openSUSE:Factory:Staging:' + pr + '/_meta'] = project_meta_change

    def _request_review(self):
        """Register requests methods."""

        # Load template
        tmpl = Template(self._get_fixture_content('request_review.xml'))

        # What happens when we try to change the review
        def review_change(responses, request, uri):
            rq_id = re.match(r'.*/([0-9]+)', uri).group(1)
            args = self.requests_data[rq_id]
            # Adding review
            if 'cmd' in request.querystring and 'addreview' in request.querystring['cmd']:
                self.requests_data[rq_id]['request'] = 'review'
                self.requests_data[rq_id]['review'] = 'new'
            # Changing review
            if 'cmd' in request.querystring and 'changereviewstate' in request.querystring[u'cmd']:
                self.requests_data[rq_id]['request'] = 'new'
                self.requests_data[rq_id]['review'] = str(request.querystring[u'newstate'][0])
            # Project review
            if 'by_project' in request.querystring:
                self.requests_data[rq_id]['by'] = 'project'
                self.requests_data[rq_id]['by_who'] = str(request.querystring[u'by_project'][0])
            # Group review
            if 'by_group' in request.querystring:
                self.requests_data[rq_id]['by'] = 'group'
                self.requests_data[rq_id]['by_who'] = str(request.querystring[u'by_group'][0])
            responses['GET']['/request/' + rq_id] = tmpl.substitute(self.requests_data[rq_id])
            return responses['GET']['/request/' + rq_id]

        # Register methods for all requests
        for rq in self.requests_data:
            # Static response for gets (just filling template from local data)
            self.responses['GET']['/request/' + rq] = tmpl.substitute(self.requests_data[rq])
            # Interpret other requests
            self.responses['ALL']['/request/' + rq] = review_change

    def _pkg_sources(self):
        def pkg_source(responses, request, uri):
            key = re.match(r'.*/source/([^?]+)(\?.*)?', uri).group(1)
            return '<directory name="{}" rev="{}" vrev="{}" srcmd5="{}"/>'.format(
                self.pkg_data[key]['name'],
                self.pkg_data[key]['rev'],
                self.pkg_data[key]['vrev'],
                self.pkg_data[key]['srcmd5']
            )
        for pkg in self.pkg_data:
            self.responses['GET']['/source/' + pkg] = pkg_source

    def _link_sources(self):
        # Load template
        tmpl = Template(self._get_fixture_content('linksource.xml'))

        def delete_link(responses, request, uri):
            key = re.match(r'.*/source/([^?]+)(\?.*)?', uri).group(1)
            del self.responses['GET']['/source/' + str(key)]
            del self.links_data[str(key)]
            return "Ok"

        def create_empty(responses, request, uri):
            key = re.match(r'.*/source/(.+)/_meta', uri).group(1)
            self.links_data[str(key)] = {}
            return "Ok"

        def create_link(responses, request, uri):
            tmpl = Template(self._get_fixture_content('linksource.xml'))
            key = re.match(r'.*/source/(.+)/_link', uri).group(1)
            match = re.match(r'(.+)/(.+)', key)
            xml = ET.fromstring(str(request.body))
            self.links_data[str(key)] = {
                'prj': match.group(1),
                'pkg': match.group(2),
                'devprj': xml.get('project')
            }
            self.responses['GET']['/source/' + key] = tmpl.substitute(self.links_data[key])
            self.responses['DELETE']['/source/' + key] = delete_link
            return "Ok"

        # Register methods for requests
        for link in self.links_data:
            self.responses['GET']['/source/' + link] = tmpl.substitute(self.links_data[link])
            self.responses['DELETE']['/source/' + link] = delete_link

        # Register method for package creation
        for pr in self.st_project_data:
            for rq in self.requests_data:
                self.responses['PUT']['/source/openSUSE:Factory:Staging:' + pr + '/' + self.requests_data[rq]['package'] + '/_meta'] = create_empty
                self.responses['PUT']['/source/openSUSE:Factory:Staging:' + pr + '/' + self.requests_data[rq]['package'] + '/_link'] = create_link

    def _search(self):
        """
        Allows searching for requests
        """
        def request_search(responses, request, uri):
            # Searching for requests that has open review for staging group
            if 'match' in request.querystring and request.querystring['match'][0] == u"state/@name='review' and review[@by_group='factory-staging' and @state='new']":
                rqs = []
                # Itereate through all requests
                for rq in self.requests_data:
                    # Find the ones matching the condition
                    if self.requests_data[rq]['request'] == 'review' and self.requests_data[rq]['review'] == 'new' and self.requests_data[rq]['by'] == 'group' and self.requests_data[rq]['by_who'] == 'factory-staging':
                        rqs.append(rq)
                # Create response
                ret_str = '<collection matches="' + str(len(rqs)) + '">'
                for rq in rqs:
                    ret_str += responses['GET']['/request/' + rq]
                ret_str += '</collection>'
                return ret_str
            # Searching for requests that has open review for staging project
            if 'match' in request.querystring and re.match(r"state/@name='review' and review\[@by_project='([^']+)' and @state='new'\]", request.querystring[u'match'][0]):
                prj_match = re.match(r"state/@name='review' and review\[@by_project='([^']+)' and @state='new'\]", request.querystring[u'match'][0])
                prj = str(prj_match.group(1))
                rqs = []
                # Itereate through all requests
                for rq in self.requests_data:
                    # Find the ones matching the condition
                    if self.requests_data[rq]['request'] == 'review' and self.requests_data[rq]['review'] == 'new' and self.requests_data[rq]['by'] == 'project' and self.requests_data[rq]['by_who'] == prj:
                        rqs.append(rq)
                # Create response
                ret_str = '<collection matches="' + str(len(rqs)) + '">\n'
                for rq in rqs:
                    ret_str += '  <request id="' + rq + '"/>\n'
                ret_str += '</collection>'
                return ret_str
            # We are searching for something else, we don't know the answer
            raise BaseException("No search results defined for " + pprint.pformat(request.querystring))

        def id_project_search(responses, request, uri):
            # Searching for project
            if 'match' in request.querystring and request.querystring['match'][0] == u"starts-with(@name,\'openSUSE:Factory:Staging:\')":
                ret_str = '<collection matches="' + str(len(self.st_project_data)) + '">\n'
                # Itereate through all requests
                for prj in self.st_project_data:
                    ret_str += '   <project name="openSUSE:Factory:Staging:' + prj + '"/>\n'
                ret_str += '</collection>'
                return ret_str
            # We are searching for something else, we don't know the answer
            raise BaseException("No search results defined for " + pprint.pformat(request.querystring))
        self.responses['GET']['/search/request'] = request_search
        self.responses['GET']['/search/request/id'] = request_search
        self.responses['GET']['/search/project/id'] = id_project_search

    def register_obs(self):
        """
        Register custom callback for HTTPretty
        """
        httpretty.register_uri(httpretty.DELETE, re.compile(r'/.*localhost.*/'), body=self._pretty_callback)
        httpretty.register_uri(httpretty.GET, re.compile(r'/.*localhost.*/'), body=self._pretty_callback)
        httpretty.register_uri(httpretty.PUT, re.compile(r'/.*localhost.*/'), body=self._pretty_callback)
        httpretty.register_uri(httpretty.POST, re.compile(r'/.*localhost.*/'), body=self._pretty_callback)
        self.reset_config()
        # Initiate the api with mocked rings
        self.api = oscs.StagingAPI('https://localhost')

    def _get_fixtures_dir(self):
        """
        Return path for fixtures
        """
        return os.path.join(os.getcwd(), 'tests/fixtures')

    def _get_fixture_path(self, filename):
        """
        Return path for fixture
        """
        return os.path.join(self._get_fixtures_dir(), filename)

    def _get_fixture_content(self, filename):
        """
        Return content of fixture
        """
        response = open(self._get_fixture_path(filename), 'r')
        content = response.read()
        response.close()
        return content
