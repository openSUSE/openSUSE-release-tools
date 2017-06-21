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

import os
import re
import subprocess
from urllib import quote_plus
import urllib2
from xml.etree import cElementTree as ET
from pprint import pformat

import osc.core
from osc.core import get_binary_file
from osc.core import http_DELETE
from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl
from osclib.core import maintainers_get
from osclib.stagingapi import StagingAPI
from osclib.memoize import memoize
from osclib.pkgcache import PkgCache


# Directory where download binary packages.
BINCACHE = os.path.expanduser('~/co')
DOWNLOADS = os.path.join(BINCACHE, 'downloads')


class Request(object):
    """Simple request container."""

    def __init__(self, request_id=None, src_project=None,
                 src_package=None, tgt_project=None, tgt_package=None,
                 revision=None, srcmd5=None, verifymd5=None,
                 group=None, goodrepos=None, missings=None,
                 is_shadow=None, shadow_src_project=None,
                 element=None, staging=None):

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
        self.missings = missings if missings else {}
        self.is_shadow = is_shadow
        self.shadow_src_project = shadow_src_project

        self.updated = False
        self.error = None
        self.build_excluded = False
        self.action_type = 'submit'  # assume default
        self.downloads = []
        self.is_shadow_devel = False
        self.i686_only = ['glibc.i686']

        if element:
            self.load(element, staging)

    def load(self, element, staging):
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
        self.missings = {}

        # Detect if the request comes from Factory to a openSUSE
        # release, and adjust the source and target projects
        _is_product = re.match(r'openSUSE:Leap:\d{2}.\d', self.tgt_project)
        if self.src_project == 'openSUSE:Factory' and _is_product:
            devel = staging.get_devel_project(self.src_project, self.src_package)
            if devel:
                self.is_shadow_devel = False
                self.shadow_src_project = devel
            else:
                self.is_shadow_devel = True
                self.shadow_src_project = '%s:Staging:repochecker' % self.tgt_project
        else:
            self.is_shadow_devel = False
            self.shadow_src_project = self.src_project

    def str_compact(self):
        s = None
        if self.action_type == 'delete':
            s = '#[%s] DELETE (%s)%s' % (
                self.request_id, self.tgt_package,
                (' Shadow via %s' % self.shadow_src_project) if self.is_shadow_devel else '')
        else:
            s = '#[%s](%s)%s' % (
                self.request_id, self.src_package,
                (' Shadow via %s' % self.shadow_src_project) if self.is_shadow_devel else '')
        return s

    def __repr__(self):
        s = None
        if self.action_type == 'delete':
            s = '#[%s] DELETE -> %s/%s%s' % (
                self.request_id,
                self.tgt_project,
                self.tgt_package,
                (' Shadow via %s' % self.shadow_src_project) if self.is_shadow_devel else '')
        else:
            s = '#[%s] %s/%s -> %s/%s%s' % (
                self.request_id,
                self.src_project,
                self.src_package,
                self.tgt_project,
                self.tgt_package,
                (' Shadow via %s' % self.shadow_src_project) if self.is_shadow_devel else '')
        return s


class CheckRepo(object):

    def __init__(self, apiurl, project, readonly=False, force_clean=False, debug=False):
        """CheckRepo constructor."""
        self.apiurl = apiurl
        self.project = project
        self.staging = StagingAPI(apiurl, self.project)

        self.pkgcache = PkgCache(BINCACHE, force_clean=force_clean)

        # grouped = { id: staging, }
        self.grouped = {}
        # groups = { staging: [ids,], }
        self.groups = {}
        self._staging()
        self.readonly = readonly
        self.debug_enable = debug
        self.accept_counts = {}
        self.accepts = {}

    def debug(self, *args):
        if not self.debug_enable:
            return
        print ' '.join([i if isinstance(i, basestring) else pformat(i) for i in args])

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

        if newstate == 'accepted':
            messages = self.accepts.get(request_id, [])
            messages.append(message)
            self.accepts[request_id] = messages

            self.accept_counts[request_id] = self.accept_counts.get(request_id, 0) + 1
            if self.accept_counts[request_id] != len(self.target_archs()):
                print('- Wait for successful reviews of all archs.')
                return 200
            else:
                message = '\n'.join(set(messages))

        code = 404
        url = makeurl(self.apiurl, ('request', str(request_id)), query=query)
        if self.readonly:
            print 'DRY RUN: POST %s' % url
            return 200
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
                request = Request(element=request, staging=self.staging)
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return request

    def pending_requests(self):
        """Search pending requests to review."""
        requests = []
        review = "@by_user='factory-repo-checker'+and+@state='new'"
        target = "@project='{}'".format(self.project)
        target_nf = "@project='{}'".format(self.staging.cnonfree)
        try:
            url = makeurl(self.apiurl, ('search', 'request'),
                          "match=state/@name='review'+and+review[%s]+and+(target[%s]+or+target[%s])" % (
                              review, target, target_nf))
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
                _request = Request(element=root, staging=self.staging)
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
        url = makeurl(self.apiurl,
                      ('build', src_project,
                       '_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s' % (
                           quote_plus(src_package),
                           quote_plus(tgt_project),
                           rev)))
        xml = http_GET(url).read()
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

        self.debug("check_specs", request_id)

        # Check that only one action is allowed in the request.
        actions = request.findall('action')
        if len(actions) > 1:
            msg = 'Only one action per request is supported'
            print('[DECLINED]', msg)
            self.change_review_state(request_id, 'declined', message=msg)
            return requests

        rq = Request(element=request, staging=self.staging)

        if rq.action_type != 'submit' and rq.action_type != 'delete':
            msg = 'Unchecked request type %s' % rq.action_type
            print 'ACCEPTED', msg
            self.change_review_state(request_id, 'accepted', message=msg)
            return requests

        if rq.src_project == 'devel:languages:haskell':
            msg = 'I give up, Haskell is too hard for me'
            print 'ACCEPTED', request_id, msg
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

        # special case for glibc.i686, it have not the relevant specfile for glibc.i686
        # but must be add it to requests list as a dummy request, otherwise the state
        # has not be check and won't download it's binaries.
        if 'glibc' in specs:
            specs.append('glibc.i686')

        # source checker already validated it
        if rq.src_package in specs:
            specs.remove(rq.src_package)
        elif rq.tgt_package in specs:
            specs.remove(rq.tgt_package)
        else:
            msg = 'The name of the SPEC files %s do not match the name of the package (%s)'
            msg = msg % (specs, rq.src_package)
            print('[DECLINED]', msg)
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

            is_src_diff = (spec_info['project'] != rq.src_project or
                           spec_info['package'] != rq.src_package)
            if is_src_diff and not rq.updated:
                msg = '%s/%s should _link to %s/%s' % (rq.src_project,
                                                       spec,
                                                       rq.src_project,
                                                       rq.src_package)
                print '[DECLINED]', msg
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
                print '[WARNING] CHECK MANUALLY', msg
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
        more_repo_candidates = []

        try:
            root_xml = self.last_build_success(request.shadow_src_project,
                                               request.tgt_project,
                                               request.src_package,
                                               request.verifymd5)
        except urllib2.HTTPError as e:
            if 300 <= e.code <= 499:
                print ' - The request is not built against this project'
                return repos_to_check
            raise e

        root = ET.fromstring(root_xml)
        archs_target = self.target_archs()
        for repo in root.findall('repository'):
            archs_found = 0
            for arch in repo.findall('arch'):
                if arch.attrib['arch'] in archs_target:
                    archs_found += 1

            if archs_found == len(archs_target):
                repos_to_check.append(repo)

        return repos_to_check

    @memoize(session=True)
    def target_archs(self, project=None):
        if not project: project = self.project

        meta = osc.core.show_project_meta(self.apiurl, project)
        meta = ET.fromstring(''.join(meta))
        archs = []
        for arch in meta.findall('repository[@name="standard"]/arch'):
            archs.append(arch.text)
        return archs

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
        key = (project, repository, arch, package, filename, mtime)
        if key in self.pkgcache:
            try:
                os.unlink(target)
            except:
                pass
            self.pkgcache.linkto(key, target)
        else:
            get_binary_file(self.apiurl, project, repository, arch,
                            filename, package=package,
                            target_filename=target)
            self.pkgcache[key] = target

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
            self._get_binary_file(_project, _repo, arch, request.src_package, fn, t, mt)

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

        # Some subpackage do not have any rpm (e.g. rpmlint)
        if not last_disturldir:
            return

        for _project, _repo, arch, fn, mt in todownload_rest:
            repodir = os.path.join(DOWNLOADS, request.src_package, _project, _repo)
            if not os.path.exists(repodir):
                os.makedirs(repodir)
            t = os.path.join(repodir, fn)
            self._get_binary_file(_project, _repo, arch, request.src_package, fn, t, mt)

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
        for arch in self.target_archs():
            for fn in self.get_package_list_from_repository(
                request.tgt_project, 'standard', arch, request.tgt_package):
                # On i586 only exclude -32bit packages.
                if fn[1] and (arch != 'i586' or fn[2] == 'x86_64'):
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

    @memoize(session=True)
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
            raise ValueError('Please, provide filename or md5_disturl')

        # ugly workaround here, glibc.i686 had a topadd block in _link, and looks like
        # it causes the disturl won't consistently with glibc even with the same srcmd5
        if request.src_package == 'glibc.i686':
            return True

        md5_disturl = md5_disturl if md5_disturl else self._md5_disturl(self._disturl(filename))
        vrev_local = self._get_verifymd5(request, md5_disturl)

        # md5_disturl == request.srcmd5 is true for packages in the devel project.
        # vrev_local == request.srcmd5 is true for kernel submission
        # vrev_local == request.verifymd5 is ture for packages from different projects
        if md5_disturl == request.srcmd5 or vrev_local in (request.srcmd5, request.verifymd5):
            return True
        else:
            msg = '%s is no longer the submitted version in %s, please recheck!' % (request.src_package, request.src_project)
            print '[WARNING] CHECK MANUALLY', msg

        return False

    def is_buildsuccess(self, request):
        """Return True if the request is correctly build

        This method extend the Request object with the goodrepos
        field.

        :param request: Request object
        :returns: True if the request is correctly build.

        """

        # If the request do not build properly in both Intel platforms,
        # return False.
        try:
            repos_to_check = self.repositories_to_check(request)
        except urllib2.HTTPError as e:
            if 500 <= e.code <= 599:
                print ' - Temporal error in OBS: %s %s' % (e.code, e.msg)
            else:
                print ' - Unknown error in OBS: %s %s' % (e.code, e.msg)
            # Ignore this request until OBS error dissapears
            request.updated = True
            return False

        if not repos_to_check:
            msg = 'Missing {} in the repo list'.format(', '.join(self.target_archs()))
            print ' - %s' % msg
            self.change_review_state(request.request_id, 'new', message=msg)
            # Next line not needed, but for documentation.
            request.updated = True
            return False

        result = False
        alldisabled = True
        foundbuilding = None
        foundfailed = None

        archs_target = self.target_archs()
        for repository in repos_to_check:
            repo_name = repository.attrib['name']
            self.debug("checking repo", ET.tostring(repository))
            isgood = True
            founddisabled = False
            r_foundbuilding = None
            r_foundfailed = None
            missings = []
            for arch in repository.findall('arch'):
                if arch.attrib['arch'] not in archs_target:
                    continue
                if arch.attrib['result'] == 'excluded':
                    if ((arch.attrib['arch'] != 'i586' and request.src_package not in request.i686_only) or
                       (arch.attrib['arch'] == 'i586' and request.src_package in request.i686_only)):
                        request.build_excluded = True
                if 'missing' in arch.attrib:
                    for package in arch.attrib['missing'].split(','):
                        if not self.is_binary(
                                request.src_project,
                                repo_name,
                                arch.attrib['arch'],
                                package):
                            missings.append(package)
                if arch.attrib['result'] not in ('succeeded', 'excluded'):
                    isgood = False
                if arch.attrib['result'] == 'disabled':
                    founddisabled = True
                if arch.attrib['result'] == 'failed' or arch.attrib['result'] == 'unknown':
                    # Sometimes an unknown status is equivalent to
                    # disabled, but we map it as failed to have a human
                    # check (no autoreject)
                    r_foundfailed = repo_name
                if arch.attrib['result'] == 'building':
                    r_foundbuilding = repo_name

                # ugly workaround here, glibc.i686 had a topadd block in _link, and looks like
                # it causes the disturl won't consistently with glibc even with the same srcmd5.
                # and the build state per srcmd5 was outdated also.
                if request.src_package == 'glibc.i686':
                    if ((arch.attrib['arch'] == 'i586' and arch.attrib['result'] == 'outdated') or
                       (arch.attrib['arch'] != 'i586' and arch.attrib['result'] == 'excluded')):
                        isgood = True
                        continue
                if arch.attrib['result'] == 'outdated':
                    msg = "%s's sources were changed after submission: the relevant binaries are not available (never built or binaries replaced). Please resubmit" % request.src_package
                    print '[DECLINED]', msg
                    self.change_review_state(request.request_id, 'declined', message=msg)
                    # Next line is not needed, but for documentation
                    request.updated = True
                    return False

            if not founddisabled:
                alldisabled = False
            if isgood:
                _goodrepo = (request.src_project, repo_name)
                self.debug("good repo", _goodrepo)
                if _goodrepo not in request.goodrepos:
                    request.goodrepos.append(_goodrepo)
                result = True
            if r_foundbuilding:
                foundbuilding = r_foundbuilding
            if r_foundfailed:
                foundfailed = r_foundfailed
            if missings:
                request.missings[repo_name] = missings

        # Need to return if result is True at this point
        # Otherwise, it will returned False at some point, eg. an unknown status
        if result:
            return True

        if alldisabled:
            msg = '%s is disabled or does not build against the target project.' % request.src_package
            print msg
            self.change_review_state(request.request_id, 'new', message=msg)
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

        return False

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
            if self.readonly:
                print 'DRY RUN: DELETE %s' % url
            else:
                http_DELETE(url)
            for sub_prj, sub_pkg in self.staging.get_sub_packages(request.src_package,
                                                                  request.shadow_src_project):
                url = makeurl(self.apiurl, ('source', sub_prj, sub_pkg))
                if self.readonly:
                    print 'DRY RUN: DELETE %s' % url
                else:
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
        for arch in self.target_archs():
            url = makeurl(self.apiurl, ('build', request.tgt_project, 'standard', arch, '_builddepinfo'),
                          query=query)
            root = ET.parse(http_GET(url)).getroot()
            deps.update(pkgdep.text for pkgdep in root.findall('.//pkgdep'))
        return deps

    def _builddepinfo(self, project, package):
        """Return the list dependencies for a request."""
        deps = set()
        query = {
            'package': package,
        }
        for arch in self.target_archs():
            url = makeurl(self.apiurl, ('build', project, 'standard', arch, '_builddepinfo'),
                          query=query)
            root = ET.parse(http_GET(url)).getroot()
            deps.update(pkgdep.text for pkgdep in root.findall('.//pkgdep'))
        return deps

    def _author(self, request):
        """Get the author of the request."""
        query = {
            'withhistory': 1,
        }
        url = makeurl(self.apiurl, ('request', str(request.request_id)), query=query)
        root = ET.parse(http_GET(url)).getroot()

        who = None
        state = root.find('state')
        try:
            if state.get('name') == 'new':
                who = state.get('who')
            else:
                who = root.find('history').get('who')
        except Exception:
            who = None
        return who

    def _project_maintainer(self, request):
        """Get the list of maintainer of the target project."""
        url = makeurl(self.apiurl, ('source', request.tgt_project, '_meta'))
        root = ET.parse(http_GET(url)).getroot()
        persons = [e.get('userid') for e in root.findall('.//person') if e.get('role') == 'maintainer']
        return persons

    def is_safe_to_delete(self, request):
        """Return True is the request is secure to remove:

        - Nothing depends on the package anymore.
        - The request originates by the package maintainer.

        """
        reasons = []
        whatdependson = self._whatdependson(request)
        maintainers = maintainers_get(self.apiurl, request.tgt_project, request.tgt_package)
        author = self._author(request)
        prj_maintainers = self._project_maintainer(request)

        for dep in whatdependson:
            deps = self._builddepinfo(request.tgt_project, dep)
            if request.tgt_package in deps:
                reasons.append('%s still depends on %s in %s' % (dep, request.tgt_package, request.tgt_project))

        if author not in maintainers and author not in prj_maintainers:
            reasons.append('The author (%s) is not one of the maintainers (%s) or a project maintainer in %s' % (
                author, ', '.join(maintainers), request.tgt_project))
        return '. '.join(reasons)
