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
import re
import subprocess
from urllib import quote_plus
import urllib2
from xml.etree import cElementTree as ET

from osc.core import get_binary_file
from osc.core import http_DELETE
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
                 is_shadow=None, shadow_src_project=None,
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
        self.is_shadow = is_shadow
        self.shadow_src_project = shadow_src_project

        self.updated = False
        self.error = None
        self.build_excluded = False
        self.is_partially_cached = False
        self.action_type = 'submit'  # assume default
        self.downloads = []
        self.is_shadow_devel = False

        if element:
            self.load(element)

    def load(self, element):
        """Load a node from a ElementTree request XML element."""
        self.request_id = int(element.get('id'))

        action = element.find('action')
        self.action_type = action.get('type')
        source = action.find('source')
        if source is not None:
            self.src_project = source.get('project')
            self.src_package = source.get('package')
            self.revision = source.get('rev')
        target = action.find('target')
        if target is not None:
            self.tgt_project = target.get('project')
            self.tgt_package = target.get('package')

        # The groups are in the CheckRepo object.
        self.group = self.request_id

        # Assigned in is_buildsuccess
        self.goodrepos = []
        self.missings = []

        # Detect if the request comes from Factory to a openSUSE
        # release, and adjust the source and target projects
        _is_product = re.match(r'openSUSE:\d{2}.\d', self.tgt_project)
        if self.src_project == 'openSUSE:Factory' and _is_product:
            self.is_shadow_devel = True
            self.shadow_src_project = '%s:Devel' % self.tgt_project
        else:
            self.is_shadow_devel = False
            self.shadow_src_project = self.src_project

    def str_compact(self):
        return '#[%s](%s)%s' % (self.request_id, self.src_package,
                                (' Shadow via %s' % self.shadow_src_project) if self.is_shadow_devel else '')

    def __repr__(self):
        return '#[%s] %s/%s -> %s/%s%s' % (self.request_id,
                                           self.src_project,
                                           self.src_package,
                                           self.tgt_project,
                                           self.tgt_package,
                                           (' Shadow via %s' % self.shadow_src_project) if self.is_shadow_devel else '')


class CheckRepo(object):

    def __init__(self, apiurl, opensuse='Factory'):
        """CheckRepo constructor."""
        self.apiurl = apiurl
        self.opensuse = opensuse
        self.staging = StagingAPI(apiurl, opensuse)

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

    def get_request_state(self, request_id):
        """Return the current state of the request."""
        state = None
        url = makeurl(self.apiurl, ('request', str(request_id)))
        try:
            root = ET.parse(http_GET(url)).getroot()
            state = root.find('state').get('name')
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return state

    def get_review_state(self, request_id):
        """Return the current review state of the request."""
        states = []
        url = makeurl(self.apiurl, ('request', str(request_id)))
        try:
            root = ET.parse(http_GET(url)).getroot()
            states = [review.get('state') for review in root.findall('review') if review.get('by_user') == 'factory-repo-checker']
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return states[0] if states else ''

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

        review_state = self.get_review_state(request_id)
        if review_state == 'accepted' and newstate != 'accepted':
            print ' - Avoid change state %s -> %s (%s)' % (review_state, newstate, message)

        code = 404
        url = makeurl(self.apiurl, ('request', str(request_id)), query=query)
        try:
            root = ET.parse(http_POST(url, data=message)).getroot()
            code = root.attrib['code']
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return code

    def get_request(self, request_id, internal=False):
        """Get a request XML or internal object."""
        request = None
        try:
            url = makeurl(self.apiurl, ('request', str(request_id)))
            request = ET.parse(http_GET(url)).getroot()
            if internal:
                request = Request(element=request)
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return request

    def pending_requests(self):
        """Search pending requests to review."""
        requests = []
        review = "@by_user='factory-repo-checker'+and+@state='new'"
        target = "@project='openSUSE:{}'".format(self.opensuse)
        try:
            url = makeurl(self.apiurl, ('search', 'request'),
                          "match=state/@name='review'+and+review[%s]+and+target[%s]" % (review, target))
            root = ET.parse(http_GET(url)).getroot()
            requests = root.findall('request')
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return requests

    def find_request_id(self, project, package):
        """Return a request id that is in new, review of accepted state for a
        specific project/package.

        """
        xpath = "(action/target/@project='%s' and "\
                "action/target/@package='%s' and "\
                "action/@type='submit' and "\
                "(state/@name='new' or state/@name='review' or "\
                "state/@name='accepted'))" % (project, package)
        query = {
            'match': xpath
        }

        request_id = None
        try:
            url = makeurl(self.apiurl, ('search', 'request'), query=query)
            collection = ET.parse(http_GET(url)).getroot()
            for root in collection.findall('request'):
                _request = Request(element=root)
                request_id = _request.request_id
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return request_id

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

        rq = Request(element=request)

        if rq.action_type != 'submit' and rq.action_type != 'delete':
            msg = 'Unchecked request type %s' % rq.action_type
            print 'ACCEPTED', msg
            self.change_review_state(request_id, 'accepted', message=msg)
            return requests

        rq.group = self.grouped.get(request_id, request_id)
        requests.append(rq)

        if rq.action_type == 'delete':
            # only track the target package
            return requests

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

        # source checker already validated it
        if rq.src_package in specs:
            specs.remove(rq.src_package)
        elif rq.tgt_package in specs:
            specs.remove(rq.tgt_package)
        else:
            msg = 'The name of the SPEC files %s do not match with the name of the package (%s)'
            msg = msg % (specs, rq.src_package)
            print('DECLINED', msg)
            self.change_review_state(request_id, 'declined', message=msg)
            rq.updated = True
            return requests

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
            except KeyError as e:
                # This exception happends some times when there is an
                # 'error' attribute in the package information XML
                rq.error = 'There is an error in the SPEC file for (%s, %s).' % (rq.src_project, spec)
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
                # self.change_review_state(id_, 'declined', message=msg)
                rq.updated = True

            sp = Request(request_id=rq.request_id,
                         src_project=rq.src_project,
                         src_package=spec,
                         tgt_project=rq.tgt_project,
                         tgt_package=spec,
                         revision=None,
                         srcmd5=rq.srcmd5,
                         verifymd5=rq.verifymd5,
                         group=rq.group,
                         is_shadow=rq.is_shadow,
                         shadow_src_project=rq.shadow_src_project)
            requests.append(sp)

        return requests

    def repositories_to_check(self, request):
        """Return the list of repositories that contains both Intel arch.

        Each repository is an XML ElementTree from last_build_success.

        """
        repos_to_check = []

        root_xml = self.last_build_success(request.shadow_src_project,
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

    def _get_binary_file(self, project, repository, arch, package, filename, target, mtime):
        """Get a binary file from OBS."""
        # Check if the file is already there.
        if os.path.exists(target):
            # we need to check the mtime too as the file might get updated
            cur = os.path.getmtime(target)
            if cur > mtime:
                return True

        get_binary_file(self.apiurl, project, repository, arch,
                        filename, package=package,
                        target_filename=target)
        return False

    def _download(self, request, todownload):
        """Download the packages referenced in the 'todownload' list."""
        last_disturl = None
        last_disturldir = None

        # We need to order the files to download.  First RPM packages (to
        # set disturl), after that the rest.

        todownload_rpm = [rpm for rpm in todownload if rpm[3].endswith('.rpm')]
        todownload_rest = [rpm for rpm in todownload if not rpm[3].endswith('.rpm')]

        for _project, _repo, arch, fn, mt in todownload_rpm:
            repodir = os.path.join(DOWNLOADS, request.src_package, _project, _repo)
            if not os.path.exists(repodir):
                os.makedirs(repodir)
            t = os.path.join(repodir, fn)
            was_cached = self._get_binary_file(_project, _repo, arch,
                                               request.src_package,
                                               fn, t, mt)
            if was_cached:
                continue

            # Organize the files into DISTURL directories.
            disturl = self._md5_disturl(self._disturl(t))
            disturldir = os.path.join(repodir, disturl)
            last_disturl, last_disturldir = disturl, disturldir
            file_in_disturl = os.path.join(disturldir, fn)
            if not os.path.exists(disturldir):
                os.makedirs(disturldir)
            try:
                os.symlink(t, file_in_disturl)
            except:
                pass
                # print 'Found previous link.'

            request.downloads[(_project, _repo, disturl)].append(file_in_disturl)

        for _project, _repo, arch, fn, mt in todownload_rest:
            repodir = os.path.join(DOWNLOADS, request.src_package, _project, _repo)
            if not os.path.exists(repodir):
                os.makedirs(repodir)
            t = os.path.join(repodir, fn)
            was_cached = self._get_binary_file(_project, _repo, arch,
                                               request.src_package,
                                               fn, t, mt)
            if was_cached:
                continue

            file_in_disturl = os.path.join(last_disturldir, fn)
            if last_disturldir:
                try:
                    os.symlink(t, file_in_disturl)
                except:
                    pass
                    # print 'Found previous link.'
            else:
                print "I don't know where to put", fn

            request.downloads[(_project, _repo, last_disturl)].append(file_in_disturl)

    def _toignore(self, request):
        """Return the list of files to ignore during the checkrepo."""
        toignore = set()
        for fn in self.get_package_list_from_repository(
                request.tgt_project, 'standard', 'x86_64', request.tgt_package):
            if fn[1]:
                toignore.add(fn[1])

        # now fetch -32bit pack list
        for fn in self.get_package_list_from_repository(
                request.tgt_project, 'standard', 'i586', request.tgt_package):
            if fn[1] and fn[2] == 'x86_64':
                toignore.add(fn[1])
        return toignore

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

    def get_missings(self, request):
        """Get the list of packages that are in missing status."""

        missings = set()

        # XXX TODO - This piece is contained in
        # is_buildsuccess(). Integrate both.
        repos_to_check = self.repositories_to_check(request)
        for repository in repos_to_check:
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
                            missings.add(package)
        return sorted(missings)

    def is_buildsuccess(self, request):
        """Return True if the request is correctly build

        This method extend the Request object with the goodrepos
        field.

        :param request: Request object
        :returns: True if the request is correctly build.

        """

        # Check if we have a local version of the package before
        # checking it.  If this is the case partially preload the
        # 'goodrepos' and 'missings' fields.
        if self.is_request_cached(request):
            request.is_partially_cached = True
            request.goodrepos = self._get_goodrepos_from_local(request)
            request.missings = self.get_missings(request)

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
        missings = set()
        alldisabled = True
        foundbuilding = None
        foundfailed = None

        for repository in repos_to_check:
            isgood = True
            founddisabled = False
            r_foundbuilding = None
            r_foundfailed = None
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
                            missings.add(package)
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

            if not founddisabled:
                alldisabled = False
            if isgood:
                _goodrepo = (request.src_project, repository.attrib['name'])
                if _goodrepo not in request.goodrepos:
                    request.goodrepos.append(_goodrepo)
                result = True
            if r_foundbuilding:
                foundbuilding = r_foundbuilding
            if r_foundfailed:
                foundfailed = r_foundfailed

        # If the request is partially cached, maybe there are some
        # content in request.missings.
        request.missings = sorted(set(request.missings) | missings)

        if result:
            return True

        if alldisabled:
            msg = '%s is disabled or does not build against factory. Please fix and resubmit' % request.src_package
            print 'DECLINED', msg
            self.change_review_state(request.request_id, 'declined', message=msg)
            # Next line not needed, but for documentation
            request.updated = True
            return False

        if foundbuilding and (request.src_package, foundbuilding) not in request.goodrepos:
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

    def get_package_list_from_repository(self, project, repository, arch, package):
        url = makeurl(self.apiurl, ('build', project, repository, arch, package))
        files = []
        try:
            binaries = ET.parse(http_GET(url)).getroot()
            for binary in binaries.findall('binary'):
                filename = binary.attrib['filename']
                mtime = int(binary.attrib['mtime'])

                result = re.match(r'(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm', filename)
                if not result:
                    if filename == 'rpmlint.log':
                        files.append((filename, '', '', mtime))
                    continue

                pname = result.group(1)
                if pname.endswith('-debuginfo') or pname.endswith('-debuginfo-32bit'):
                    continue
                if pname.endswith('-debugsource'):
                    continue
                if result.group(4) == 'src':
                    continue

                files.append((filename, pname, result.group(4), mtime))
        except urllib2.HTTPError:
            pass
            # print " - WARNING: Can't found list of packages (RPM) for %s in %s (%s, %s)" % (
            #     package, project, repository, arch)
        return files

    def remove_link_if_shadow_devel(self, request):
        """If the request is a shadow_devel (the reference is to a request
        that is a link from the product to Factory), remove the link
        to transform it as a normal request.

        """
        if request.is_shadow_devel:
            url = makeurl(self.apiurl, ('source', request.shadow_src_project, request.src_package))
            http_DELETE(url)
            for sub_prj, sub_pkg in self.staging.get_sub_packages(request.src_package,
                                                                  request.shadow_src_project):
                url = makeurl(self.apiurl, ('source', sub_prj, sub_pkg))
                http_DELETE(url)

    def _whatdependson(self, request):
        """Return the list of packages that depends on the one in the
        request.

        """
        deps = set()
        query = {
            'package': request.tgt_package,
            'view': 'revpkgnames',
        }
        for arch in ('i586', 'x86_64'):
            url = makeurl(self.apiurl, ('build', request.tgt_project, 'standard', arch, '_builddepinfo'),
                          query=query)
            root = ET.parse(http_GET(url)).getroot()
            deps.update(pkgdep.text for pkgdep in root.findall('.//pkgdep'))
        return deps

    def _maintainers(self, request):
        """Get the maintainer of the package involved in the request."""
        query = {
            'binary': request.tgt_package,
        }
        url = makeurl(self.apiurl, ('search', 'owner'), query=query)
        root = ET.parse(http_GET(url)).getroot()
        return [p.get('name') for p in root.findall('.//person') if p.get('role') == 'maintainer']

    def _author(self, request):
        """Get the author of the request."""
        url = makeurl(self.apiurl, ('request', str(request.request_id)))
        root = ET.parse(http_GET(url)).getroot()

        state = root.find('state')
        if state.get('name') == 'new':
            return state.get('who')
        return root.find('history').get('who')

    def is_secure_to_delete(self, request):
        """Return True is the request is secure to remove:

        - Nothing depends on the package anymore.
        - The request originates by the package maintainer.

        """
        whatdependson = self._whatdependson(request)
        maintainers = self._maintainers(request)
        author = self._author(request)

        return (not whatdependson) and author in maintainers
