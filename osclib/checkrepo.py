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

from collections import defaultdict
import os
import subprocess
from urllib import quote_plus
import urllib2
from xml.etree import cElementTree as ET

from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl
from osclib.stagingapi import StagingAPI
from osclib.memoize import memoize


# Directory where download binary packages.
DOWNLOADS = os.path.expanduser('~/co/downloads')


class Request(object):
    """Simple request container."""

    def __init__(self, request_id=None, src_project=None,
                 src_package=None, tgt_project=None, tgt_package=None,
                 revision=None, srcmd5=None, verifymd5=None,
                 group=None, goodrepos=None, missings=None,
                 element=None):

        self.request_id = request_id
        self.src_project = src_project
        self.src_package = src_package
        self.tgt_project = tgt_project
        self.tgt_package = tgt_package
        self.revision = revision
        self.srcmd5 = srcmd5
        self.verifymd5 = verifymd5
        self.group = group
        self.goodrepos = goodrepos if goodrepos else []
        self.missings = missings if missings else []

        self.updated = False
        self.error = None
        self.build_excluded = False
        self.is_cached = False

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

        # Assigned in is_buildsuccess
        self.goodrepos = []
        self.missings = []

    def str_compact(self):
        return '#[%s](%s)' % (self.request_id, self.src_package)

    def __repr__(self):
        return '#[%s] %s/%s -> %s/%s' % (self.request_id,
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
        url = makeurl(self.apiurl, ('request', str(request_id)), query=query)
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

    def _build(self, project, repository, arch, package):
        """Return the build XML document from OBS."""
        xml = ''
        try:
            url = makeurl(self.apiurl, ('build', project, repository, arch, package))
            xml = http_GET(url).read()
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return xml

    @memoize()
    def build(self, project, repository, arch, package):
        """Return the build XML document from OBS."""
        return self._build(project, repository, arch, package)

    def _last_build_success(self, src_project, tgt_project, src_package, rev):
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

    @memoize()
    def last_build_success(self, src_project, tgt_project, src_package, rev):
        """Return the last build success XML document from OBS."""
        return self._last_build_success(src_project, tgt_project, src_package, rev)

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
        rq.group = self.grouped.get(request_id, request_id)
        requests.append(rq)

        # Get source information about the SR:
        #   - Source MD5
        #   - Entries (.tar.gz, .changes, .spec ...) and MD5
        query = {
            'rev': rq.revision,
            'expand': 1
        }
        try:
            url = makeurl(self.apiurl, ['source', rq.src_project, rq.src_package],
                          query=query)
            root = ET.parse(http_GET(url)).getroot()
        except urllib2.HTTPError, e:
            print 'ERROR in URL %s [%s]' % (url, e)
            return requests

        rq.srcmd5 = root.attrib['srcmd5']
        rq.verifymd5 = self._get_verifymd5(rq, rq.srcmd5)
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
            try:
                spec_info = self.staging.get_package_information(rq.src_project, spec)
            except urllib2.HTTPError as e:
                rq.error = "Can't gather package information for (%s, %s)" % (rq.src_project, spec)
                rq.updated = True
                continue

            if (spec_info['project'] != rq.src_project
               or spec_info['package'] != rq.src_package) and not rq.updated:
                msg = '%s/%s should _link to %s/%s' % (rq.src_project,
                                                       spec,
                                                       rq.src_project,
                                                       rq.src_package)
                print 'DECLINED', msg
                self.change_review_state(rq.request_id, 'declined', message=msg)
                rq.updated = True

            if spec_info['srcmd5'] != rq.srcmd5 and not rq.updated:
                if spec_info['srcmd5'] not in self.old_md5(rq.src_project,
                                                           rq.tgt_project,
                                                           spec,
                                                           rq.srcmd5):
                    msg = '%s/%s is a link but has a different md5sum than %s?' % (
                        rq.src_project,
                        spec,
                        rq.src_package)
                else:
                    msg = '%s is no longer the submitted version, please resubmit HEAD' % spec
                print '[DECLINED] CHECK MANUALLY', msg
                # self.checkrepo.change_review_state(id_, 'declined', message=msg)
                rq.updated = True

            sp = Request(request_id=rq.request_id,
                         src_project=rq.src_project,
                         src_package=spec,
                         tgt_project=rq.tgt_project,
                         tgt_package=spec,
                         revision=None,
                         srcmd5=rq.srcmd5,
                         verifymd5=rq.verifymd5,
                         group=rq.group)
            requests.append(sp)

        return requests

    def repositories_to_check(self, request):
        """Return the list of repositories that contains both Intel arch.

        Each repository is an XML ElementTree from last_build_success.

        """
        repos_to_check = []

        root_xml = self.last_build_success(request.src_project,
                                           request.tgt_project,
                                           request.src_package,
                                           request.verifymd5)

        if root_xml:
            root = ET.fromstring(root_xml)
        else:
            print ' - The request is not built agains this project'
            return repos_to_check

        for repo in root.findall('repository'):
            intel_archs = [a for a in repo.findall('arch')
                           if a.attrib['arch'] in ('i586', 'x86_64')]
            if len(intel_archs) == 2:
                repos_to_check.append(repo)

        return repos_to_check

    def is_binary(self, project, repository, arch, package):
        """Return True if is a binary package."""
        root_xml = self.build(project, repository, arch, package)
        root = ET.fromstring(root_xml)
        for binary in root.findall('binary'):
            # If there are binaries, we're out.
            return False
        return True

    def _disturl(self, filename):
        """Get the DISTURL from a RPM file."""
        pid = subprocess.Popen(
            ('rpm', '--nosignature', '--queryformat', '%{DISTURL}', '-qp', filename),
            stdout=subprocess.PIPE, close_fds=True)
        os.waitpid(pid.pid, 0)[1]
        disturl = pid.stdout.readlines()[0]
        return disturl

    def _md5_disturl(self, disturl):
        """Get the md5 from the DISTURL from a RPM file."""
        return os.path.basename(disturl).split('-')[0]

    def _get_verifymd5(self, request, revision):
        """Return the verifymd5 attribute from a request."""
        query = {
            'view': 'info',
            'rev': revision,
        }
        verifymd5 = ''
        try:
            url = makeurl(self.apiurl, ('source', request.src_project, request.src_package),
                          query=query)
            root = ET.parse(http_GET(url)).getroot()
            verifymd5 = root.attrib['verifymd5']
        except urllib2.HTTPError, e:
            print 'ERROR in URL %s [%s]' % (url, e)
        return verifymd5

    def check_disturl(self, request, filename=None, md5_disturl=None):
        """Try to match the srcmd5 of a request with the one in the RPM package."""
        if not filename and not md5_disturl:
            raise ValueError('Please, procide filename or md5_disturl')

        md5_disturl = md5_disturl if md5_disturl else self._md5_disturl(self._disturl(filename))

        # This is true for packages in the devel project.
        if md5_disturl == request.srcmd5:
            return True

        vrev_local = self._get_verifymd5(request, md5_disturl)

        # For the kernel (maybe because is not linked)
        if vrev_local == request.srcmd5:
            return True

        # For packages from different projects (devel and staging)
        if vrev_local == request.verifymd5:
            return True

        return False

    def is_request_cached(self, request):
        """Search the request in the local cache."""
        result = False

        package_dir = os.path.join(DOWNLOADS, request.src_package)
        rpm_packages = []
        for dirpath, dirnames, filenames in os.walk(package_dir):
            rpm_packages.extend(os.path.join(dirpath, f) for f in filenames if f.endswith('.rpm'))

        result = any(self.check_disturl(request, filename=rpm) for rpm in rpm_packages)

        return result

    def _get_goodrepos_from_local(self, request):
        """Calculate 'goodrepos' from local cache."""

        # 'goodrepos' store the tuples (project, repos)
        goodrepos = []

        package_dir = os.path.join(DOWNLOADS, request.src_package)
        projects = os.walk(package_dir).next()[1]

        for project in projects:
            project_dir = os.path.join(package_dir, project)
            repos = os.walk(project_dir).next()[1]

            for repo in repos:
                goodrepos.append((project, repo))

        return goodrepos

    def _get_downloads_from_local(self, request):
        """Calculate 'downloads' from local cache."""
        downloads = defaultdict(list)

        package_dir = os.path.join(DOWNLOADS, request.src_package)

        for project, repo in self._get_goodrepos_from_local(request):
            repo_dir = os.path.join(package_dir, project, repo)
            disturls = os.walk(repo_dir).next()[1]

            for disturl in disturls:
                disturl_dir = os.path.join(DOWNLOADS, request.src_package, project, repo, disturl)
                filenames = os.walk(disturl_dir).next()[2]
                downloads[(project, repo, disturl)] = [os.path.join(disturl_dir, f) for f in filenames]

        return downloads

    def is_buildsuccess(self, request):
        """Return True if the request is correctly build

        This method extend the Request object with the goodrepos
        field.

        :param request: Request object
        :returns: True if the request is correctly build.

        """

        # Check if we have a local version of the package before
        # checking it.
        if self.is_request_cached(request):
            request.is_cached = True
            request.goodrepos = self._get_goodrepos_from_local(request)
            return True

        # If the request do not build properly in both Intel platforms,
        # return False.
        repos_to_check = self.repositories_to_check(request)
        if not repos_to_check:
            msg = 'Missing i586 and x86_64 in the repo list'
            print ' - %s' % msg
            self.change_review_state(request.request_id, 'new', message=msg)
            # Next line not needed, but for documentation.
            request.updated = True
            return False

        result = False
        missings = {}
        alldisabled = True
        foundbuilding = None
        foundfailed = None

        for repository in repos_to_check:
            isgood = True
            founddisabled = False
            r_foundbuilding = None
            r_foundfailed = None
            r_missings = {}
            for arch in repository.findall('arch'):
                if arch.attrib['arch'] not in ('i586', 'x86_64'):
                    continue
                if 'missing' in arch.attrib:
                    for package in arch.attrib['missing'].split(','):
                        if not self.is_binary(
                                request.src_project,
                                repository.attrib['name'],
                                arch.attrib['arch'],
                                package):
                            missings[package] = 1
                if arch.attrib['result'] not in ('succeeded', 'excluded'):
                    isgood = False
                if arch.attrib['result'] == 'excluded' and arch.attrib['arch'] == 'x86_64':
                    request.build_excluded = True
                if arch.attrib['result'] == 'disabled':
                    founddisabled = True
                if arch.attrib['result'] == 'failed' or arch.attrib['result'] == 'unknown':
                    # Sometimes an unknown status is equivalent to
                    # disabled, but we map it as failed to have a human
                    # check (no autoreject)
                    r_foundfailed = repository.attrib['name']
                if arch.attrib['result'] == 'building':
                    r_foundbuilding = repository.attrib['name']
                if arch.attrib['result'] == 'outdated':
                    msg = "%s's sources were changed after submissions and the old sources never built. Please resubmit" % request.src_package
                    print 'DECLINED', msg
                    self.change_review_state(request.request_id, 'declined', message=msg)
                    # Next line is not needed, but for documentation
                    request.updated = True
                    return False

            r_missings = r_missings.keys()
            for pkg in r_missings:
                missings[pkg] = 1
            if not founddisabled:
                alldisabled = False
            if isgood:
                request.goodrepos.append((request.src_project, repository.attrib['name']))
                result = True
            if r_foundbuilding:
                foundbuilding = r_foundbuilding
            if r_foundfailed:
                foundfailed = r_foundfailed

        request.missings = sorted(missings)

        if result:
            return True

        if alldisabled:
            msg = '%s is disabled or does not build against factory. Please fix and resubmit' % request.src_package
            print 'DECLINED', msg
            self.change_review_state(request.request_id, 'declined', message=msg)
            # Next line not needed, but for documentation
            request.updated = True
            return False

        if foundbuilding:
            msg = '%s is still building for repository %s' % (request.src_package, foundbuilding)
            print ' - %s' % msg
            self.change_review_state(request.request_id, 'new', message=msg)
            # Next line not needed, but for documentation
            request.updated = True
            return False

        if foundfailed:
            msg = '%s failed to build in repository %s - not accepting' % (request.src_package, foundfailed)
            # failures might be temporary, so don't autoreject but wait for a human to check
            print ' - %s' % msg
            self.change_review_state(request.request_id, 'new', message=msg)
            # Next line not needed, but for documentation
            request.updated = True
            return False

        return True
