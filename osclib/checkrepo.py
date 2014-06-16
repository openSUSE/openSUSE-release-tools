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

from urllib import quote_plus
import urllib2
from xml.etree import cElementTree as ET

from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl
from osclib.stagingapi import StagingAPI
from osclib.memoize import memoize


class Request(object):
    """Simple request container."""

    def __init__(self, request_id=None, src_project=None,
                 src_package=None, tgt_project=None, tgt_package=None,
                 revision=None, srcmd5=None, group=None, element=None):

        self.request_id = request_id
        self.src_project = src_project
        self.src_package = src_package
        self.tgt_project = tgt_project
        self.tgt_package = tgt_package
        self.revision = revision
        self.srcmd5 = srcmd5
        self.group = group

        self.updated = False
        self.error = None
        self.build_excluded = False

        if element:
            self.load(element)

    def load(self, element):
        """Load a node from a ElementTree request XML element."""
        self.request_id = int(element.get('id'))

        action = element.find('action')
        self.src_project = action.find('source').get('project')
        self.src_package = action.find('source').get('package')
        self.revision = action.find('source').get('rev')
        self.tgt_project = action.find('target').get('project')
        self.tgt_package = action.find('target').get('package')
        # The groups are in the CheckRepo object.
        self.group = self.request_id

    def __repr__(self):
        return 'SUBMIT(%s) %s/%s -> %s/%s' % (self.request_id,
                                              self.src_project,
                                              self.src_package,
                                              self.tgt_project,
                                              self.tgt_package)


class CheckRepo(object):

    def __init__(self, apiurl):
        """CheckRepo constructor."""
        self.apiurl = apiurl
        self.staging = StagingAPI(apiurl)

        # grouped = { id: staging, }
        self.grouped = {}
        # groups = { staging: [ids,], }
        self.groups = {}
        self._staging()

    def _staging(self):
        """Preload the groups of related request associated by the same
        staging project.

        """
        for project in self.staging.get_staging_projects():
            # Get all the requests identifier for the project
            requests = self.staging.get_prj_pseudometa(project)['requests']
            requests = [req['id'] for req in requests]

            # Note: Originally we recover also the request returned by
            # list_requests_in_prj().  I guest that if the staging
            # project is working properly, this method do not add any
            # new request to the list.
            if requests:
                self.groups[project] = requests
                self.grouped.update({req: project for req in requests})

    def change_review_state(self, request_id, newstate, message=''):
        """Based on osc/osc/core.py. Fixed 'by_user'."""
        query = {
            'cmd': 'changereviewstate',
            'newstate': newstate,
            # XXX TODO - We force the user here, check if the user
            # expressed in .oscrc (with the password stored) have
            # rights to become this user.
            'by_user': 'factory-repo-checker',
        }

        code = 404
        url = makeurl(self.apiurl, ['request', str(request_id)], query=query)
        try:
            root = ET.parse(http_POST(url, data=message)).getroot()
            code = root.attrib['code']
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return code

    def get_request(self, request_id):
        """Get a request XML onject."""
        request = None
        try:
            url = makeurl(self.apiurl, ('request', str(request_id)))
            request = ET.parse(http_GET(url)).getroot()
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return request

    def pending_requests(self):
        """Search pending requests to review."""
        requests = []
        where = "@by_user='factory-repo-checker'+and+@state='new'"
        try:
            url = makeurl(self.apiurl, ('search', 'request'),
                          "match=state/@name='review'+and+review[%s]" % where)
            root = ET.parse(http_GET(url)).getroot()
            requests = root.findall('request')
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return requests

    @memoize()
    def build(self, project, repo, arch, package):
        """Return the build XML document from OBS."""
        xml = ''
        try:
            url = makeurl(self.apiurl, ('build', project, repo, arch, package))
            xml = http_GET(url).read()
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return xml

    @memoize()
    def last_build_success(self, src_project, tgt_project, src_package, rev):
        """Return the last build success XML document from OBS."""
        xml = ''
        try:
            url = makeurl(self.apiurl,
                          ('build', src_project,
                           '_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s' % (
                               quote_plus(src_package),
                               quote_plus(tgt_project),
                               rev)))
            xml = http_GET(url).read()
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return xml

    def get_project_repos(self, src_project, tgt_project, src_package, rev):
        """Read the repositories of the project from _meta."""
        # XXX TODO - Shitty logic here. A better proposal is refactorize
        # _check_repo_buildsuccess.
        repos = []
        url = makeurl(self.apiurl,
                      ('build', src_project,
                       '_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s' % (
                           quote_plus(src_package),
                           quote_plus(tgt_project),
                           rev)))
        try:
            root = ET.parse(http_GET(url)).getroot()
            for element in root.findall('repository'):
                archs = [(e.get('arch'), e.get('result')) for e in element.findall('arch')]
                repos.append((element.get('name'), archs))
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return repos

    def old_md5(self, src_project, tgt_project, src_package, rev):
        """Recollect old MD5 for a package."""
        # XXX TODO - instead of fixing the limit, use endtime to make
        # sure that we have the correct time frame.
        limit = 20
        query = {
            'package': src_package,
            # 'code': 'succeeded',
            'limit': limit,
        }

        repositories = self.get_project_repos(src_project,
                                              tgt_project,
                                              src_package, rev)

        srcmd5_list = []
        for repository, archs in repositories:
            for arch, status in archs:
                if srcmd5_list:
                    break
                if status not in ('succeeded', 'outdated'):
                    continue

                url = makeurl(self.apiurl, ('build', src_project,
                                            repository, arch,
                                            '_jobhistory'),
                              query=query)
                try:
                    root = ET.parse(http_GET(url)).getroot()
                    srcmd5_list = [e.get('srcmd5') for e in root.findall('jobhist')]
                except urllib2.HTTPError, e:
                    print('ERROR in URL %s [%s]' % (url, e))

        md5_set = set()
        for srcmd5 in srcmd5_list:
            query = {
                'expand': 1,
                'rev': srcmd5,
            }
            url = makeurl(self.apiurl, ('source', src_project, src_package), query=query)
            root = ET.parse(http_GET(url)).getroot()
            md5_set.add(root.find('linkinfo').get('srcmd5'))

        return md5_set

    def check_specs(self, request_id=None, request=None):
        """Check a single request and load the different SPECs files.

        This method have side effects, it can ACCEPT or DECLINE
        requests after some checks.

        """

        requests = []

        if request_id:
            request_id = int(request_id)
            request = self.get_request(request_id)
        elif request:
            request_id = int(request.get('id'))
        else:
            raise Exception('Please, provide a request_id or a request XML object.')

        # Check that only one action is allowed in the request.
        actions = request.findall('action')
        if len(actions) > 1:
            msg = 'Only one action per request is supported'
            print('DECLINED', msg)
            self.change_review_state(request_id, 'declined', message=msg)
            return requests

        # Accept requests that are not SUBMIT type.
        # XXX TODO - DELETE requests need to be managed here too.
        action = actions[0]
        action_type = action.get('type')
        if action_type != 'submit':
            msg = 'Unchecked request type %s' % action_type
            print 'ACCEPTED', msg
            self.change_review_state(request_id, 'accepted', message=msg)
            return requests

        rq = Request(element=request)
        print rq
        rq.group = self.grouped.get(request_id, request_id)
        requests.append(rq)

        # Get source information about the SR:
        #   - Source MD5
        #   - Entries (.tar.gz, .changes, .spec ...) and MD5
        try:
            url = makeurl(self.apiurl, ['source', rq.src_project, rq.src_package],
                          {'rev': rq.revision, 'expand': 1})
            root = ET.parse(http_GET(url)).getroot()
        except urllib2.HTTPError, e:
            print 'ERROR in URL %s [%s]' % (url, e)
            return requests

        rq.srcmd5 = root.attrib['srcmd5']

        # Recover the .spec files
        specs = [en.attrib['name'][:-5] for en in root.findall('entry')
                 if en.attrib['name'].endswith('.spec')]

        # source checker validated it exists
        specs.remove(rq.src_package)

        # Makes sure that the .spec file builds properly.

        # In OBS the source container is the place where all the .spec
        # files and .tgz files are stored, and used to build a binary
        # package (.RPM) and a source package (.SRC.RPM)
        #
        # There are some rules in OBS here that we need to know:
        #
        #  - There must be a .spec file that have the same name that
        #    the source container. For example, if the source
        #    container is python3-Pillow, we need a
        #    python3-Pillow.spec file.
        #
        #  - If there are more .spec files, in case that we want to
        #  - build more packages, this is represented as a new source
        #  - container in OBS, that is a link to the original one but
        #  - with the name of the .spec file.

        for spec in specs:
            spec_info = self.staging.get_package_information(rq.src_project, spec)

            if (spec_info['project'] != rq.src_project
               or spec_info['package'] != rq.src_package) and not rq.updated:
                msg = '%s/%s should _link to %s/%s' % (rq.src_project, spec, rq.src_project, rq.src_package)
                print 'DECLINED', msg
                self.change_review_state(rq.request_id, 'declined', message=msg)
                rq.updated = True

            if spec_info['srcmd5'] != rq.srcmd5 and not rq.updated:
                if spec_info['srcmd5'] not in self.old_md5(rq.src_project, rq.tgt_project, spec, rq.srcmd5):
                    msg = '%s/%s is a link but has a different md5sum than %s?' % (rq.src_project, spec, rq.src_package)
                else:
                    msg = '%s is no longer the submitted version, please resubmit HEAD' % spec
                print '[DECLINED] CHECK MANUALLY', msg
                # self.checkrepo.change_review_state(id_, 'declined', message=msg)
                rq.updated = True

            sp = Request(request_id=rq.request_id,
                         src_project=rq.src_project, src_package=spec,
                         tgt_project=rq.tgt_project, tgt_package=spec,
                         revision=None, srcmd5=spec_info['dir_srcmd5'],
                         group=rq.group)
            requests.append(sp)

        return requests
