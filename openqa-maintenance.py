#!/usr/bin/python
# Copyright (c) 2015-2017 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import os.path as opa
import re
import sys
from datetime import date
import md5
import cmdln

import simplejson as json
from simplejson import JSONDecodeError

import logging
import requests
from collections import namedtuple
from pprint import pformat
try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import gzip
from tempfile import NamedTemporaryFile
import osc.conf
import osc.core
from pprint import pprint
from osclib.comments import CommentAPI

import ReviewBot

from openqa_client.client import OpenQA_Client
from openqa_client import exceptions as openqa_exceptions

Package = namedtuple('Package', ('name', 'version', 'release'))

pkgname_re = re.compile(r'(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^.]+)\.rpm')

# QA Results
QA_UNKNOWN = 0
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3

comment_marker_re = re.compile(r'<!-- openqa state=(?P<state>done|seen)(?: result=(?P<result>accepted|declined|none))?(?: revision=(?P<revision>\d+))? -->')

logger = None

incident_name_cache = {}

# old stuff, for reference
#    def filterchannel(self, apiurl, prj, packages):
#        """ filter list of package objects to only include those actually released into prj"""
#
#        prefix = 'SUSE:Updates:'
#        logger.debug(prj)
#        if not prj.startswith(prefix):
#            return packages
#
#        channel = prj[len(prefix):].replace(':', '_')
#
#        url = osc.core.makeurl(apiurl, ('source',  'SUSE:Channels', channel, '_channel'))
#        root = ET.parse(osc.core.http_GET(url)).getroot()
#
#        package_names = set([p.name for p in packages])
#        in_channel = set([p.attrib['name'] for p in root.iter('binary') if p.attrib['name'] in package_names])
#
#        return [p for p in packages if p.name in in_channel]

data_path = opa.abspath(opa.dirname(sys.argv[0]))

with open(opa.join(data_path, "data/kgraft.json"), 'r') as f:
    KGRAFT_SETTINGS = json.load(f)

with open(opa.join(data_path, "data/repos.json"), 'r') as f:
    TARGET_REPO_SETTINGS = json.load(f)

with open(opa.join(data_path, "data/apimap.json"), 'r') as f:
    API_MAP = json.load(f)


class Update(object):

    def __init__(self, settings):
        self._settings = settings
        self._settings['_NOOBSOLETEBUILD'] = '1'

    def get_max_revision(self, job):
        repo = self.repo_prefix() + '/'
        repo += self.maintenance_project().replace(':', ':/')
        repo += ':/%s' % str(job['id'])
        max_revision = 0
        for channel in job['channels']:
            crepo = repo + '/' + channel.replace(':', '_')
            xml = requests.get(crepo + '/repodata/repomd.xml')
            if not xml.ok:
                # if one fails, we skip it and wait
                print crepo, 'has no repodata - waiting'
                return None
            root = ET.fromstring(xml.text)
            rev = root.find('.//{http://linux.duke.edu/metadata/repo}revision')
            rev = int(rev.text)
            if rev > max_revision:
                max_revision = rev
        return max_revision

    def settings(self, src_prj, dst_prj, packages):
        s = self._settings.copy()

        # start with a colon so it looks cool behind 'Build' :/
        s['BUILD'] = ':' + src_prj.split(':')[-1]
        name = self.incident_name(src_prj)
        repo = dst_prj.replace(':', '_')
        repo = '%s/%s/%s/' % (self.repo_prefix(), src_prj.replace(':', ':/'), repo)
        patch_id = self.patch_id(repo)
        if patch_id:
            s['INCIDENT_REPO'] = repo
            s['INCIDENT_PATCH'] = self.patch_id(repo)
        s['BUILD'] += ':' + name
        return s

    # grab the updateinfo from the given repo and return its patch's id
    def patch_id(self, repo):
        url = repo + 'repodata/repomd.xml'
        repomd = requests.get(url)
        if not repomd.ok:
            return None
        root = ET.fromstring(repomd.text)

        cs = root.find(
            './/{http://linux.duke.edu/metadata/repo}data[@type="updateinfo"]/{http://linux.duke.edu/metadata/repo}location')
        url = repo + cs.attrib['href']

        # python 3 brings gzip.decompress, but with python 2 we need to store to
        # temporary file to uncompress
        repomd = requests.get(url).content
        tfile = NamedTemporaryFile()
        tfile.write(repomd)
        tfile.flush()
        with gzip.open(tfile.name, 'rb') as f:
            repomd = f.read()
        root = ET.fromstring(repomd)
        return root.find('.//id').text

    # take the first package name we find - often enough correct
    def incident_name(self, prj):
        if prj not in incident_name_cache:
            incident_name_cache[prj] = self._incident_name(prj)
        return incident_name_cache[prj]

    def _incident_name(self, prj):
        shortest_pkg = None
        for package in osc.core.meta_get_packagelist(self.apiurl, prj):
            if package.startswith('patchinfo'):
                continue
            if package.endswith('SUSE_Channels'):
                continue
            url = osc.core.makeurl(
                self.apiurl,
                ('source', prj, package, '_link'))
            root = ET.parse(osc.core.http_GET(url)).getroot()
            if root.attrib.get('cicount'):
                continue
            if not shortest_pkg or len(package) < len(shortest_pkg):
                shortest_pkg = package
        if not shortest_pkg:
            shortest_pkg = 'unknown'
        match = re.match(r'^(.*)\.[^\.]*$', shortest_pkg)
        if match:
            return match.group(1)
        return shortest_pkg

    def calculate_lastest_good_updates(self, openqa, settings):
        j = openqa.openqa_request(
            'GET', 'jobs',
            {
                'distri': settings['DISTRI'],
                'version': settings['VERSION'],
                'arch': settings['ARCH'],
                'flavor': 'Updates',
                'scope': 'current',
                'limit': 100  # this needs increasing if we ever get *monster* coverage for released updates
            })['jobs']
        # check all publishing jobs per build and reject incomplete builds
        builds = {}
        for job in j:
            if 'PUBLISH_HDD_1' not in job['settings']:
                continue
            if job['result'] == 'passed' or job['result'] == 'softfailed':
                builds.setdefault(job['settings']['BUILD'], 'passed')
            else:
                builds[job['settings']['BUILD']] = 'failed'

        # take the last one passing completely
        lastgood_prefix = 0
        lastgood_suffix = 0
        for build, status in builds.items():
            if status == 'passed':
                try:
                    prefix = int(build.split('-')[0])
                    suffix = int(build.split('-')[1])
                    if prefix > lastgood_prefix:
                        lastgood_prefix = prefix
                        lastgood_suffix = suffix
                    elif prefix == lastgood_prefix and suffix > lastgood_suffix:
                        lastgood_suffix = suffix
                except ValueError:
                    continue

        if lastgood_prefix:
            settings['LATEST_GOOD_UPDATES_BUILD'] = "%d-%d" % (lastgood_prefix, lastgood_suffix)

class SUSEUpdate(Update):

    def repo_prefix(self):
        return 'http://download.suse.de/ibs'

    def maintenance_project(self):
        return 'SUSE:Maintenance'

    # we take requests that have a kgraft-patch package as kgraft patch (suprise!)
    def kgraft_target(self, prj):
        target = None
        action = None
        skip = False
        pattern = re.compile(r"kgraft-patch-([^.]+)\.")

        for package in osc.core.meta_get_packagelist(self.apiurl, prj):
            if package.startswith("kernel-"):
                skip = True
                break
            match = re.match(pattern, package)
            if match:
                target = match.group(1)
        if skip:
            return None, None

        return target

    @staticmethod
    def parse_kgraft_version(kgraft_target):
        return kgraft_target.lstrip('SLE').split('_')[0]

    @staticmethod
    def kernel_target(req):
        if req:
            for a in req.actions:
                # kernel incidents have kernel-source package (suprise!)
                if a.src_package.startswith('kernel-source'):
                    return True, a
        return None, None

    def settings(self, src_prj, dst_prj, packages):
        settings = super(SUSEUpdate, self).settings(src_prj, dst_prj, packages)
        if not settings:
            return None

        # special handling for kgraft and kernel incidents
        if settings['FLAVOR'] in ('KGraft', 'Server-DVD-Incidents-Kernel'):
            kgraft_target = self.kgraft_target(src_prj)
        # Server-DVD-Incidents-Incidents handling
        if settings['FLAVOR'] == 'Server-DVD-Incidents-Kernel':
            kernel_target = self.kernel_target(src_prj)
            if kernel_target or kgraft_target:
                # incident_id as part of BUILD
                if kgraft_target:
                    incident_id = re.match(r".*:(\d+)$", src_prj).group(1)
                    name = '.kgraft.'
                    settings['KGRAFT'] = '1'
                else:
                    incident_id = re.match(r".*:(\d+)$", src_prj).group(1)
                    name = '.kernel.'

                # discard jobs without 'start'
                settings['start'] = True
                settings['BUILD'] = ':' + req.reqid + name + incident_id
                if kgraft_target:
                    settings['VERSION'] = self.parse_kgraft_version(kgraft_target)
        # ignore kgraft patches without defined target
        # they are actually only the base for kgraft
        if settings['FLAVOR'] == 'KGraft' and kgraft_target and kgraft_target in KGRAFT_SETTINGS:
            incident_id = re.match(r".*:(\d+)$", src_prj).group(1)
            settings.update(KGRAFT_SETTINGS[kgraft_target])
            settings['BUILD'] = ':kgraft.' + incident_id
            #TODO settings['MAINT_UPDATE_RRID'] = src_prj + ':' + req.reqid

        return settings


class openSUSEUpdate(Update):

    def repo_prefix(self):
        return 'http://download.opensuse.org/repositories'

    def maintenance_project(self):
        return 'openSUSE:Maintenance'

    def settings(self, src_prj, dst_prj, packages):
        settings = super(openSUSEUpdate, self).settings(src_prj, dst_prj, packages)

        # openSUSE:Maintenance key
        settings['IMPORT_GPG_KEYS'] = 'gpg-pubkey-b3fd7e48-5549fd0f'
        settings['ZYPPER_ADD_REPO_PREFIX'] = 'incident'

        if packages:
            # XXX: this may fail in various ways
            # - conflicts between subpackages
            # - added packages
            # - conflicts with installed packages (e.g sendmail vs postfix)
            settings['INSTALL_PACKAGES'] = ' '.join(set([p.name for p in packages]))
            settings['VERIFY_PACKAGE_VERSIONS'] = ' '.join(
                ['{} {}-{}'.format(p.name, p.version, p.release) for p in packages])

        settings['ZYPPER_ADD_REPOS'] = settings['INCIDENT_REPO']
        settings['ADDONURL'] = settings['INCIDENT_REPO']

        settings['WITH_MAIN_REPO'] = 1
        settings['WITH_UPDATE_REPO'] = 1

        return settings


PROJECT_OPENQA_SETTINGS = {}

with open(opa.join(data_path, "data/incidents.json"), 'r') as f:
    for i, j in json.load(f).items():
        if i.startswith('SUSE'):
            PROJECT_OPENQA_SETTINGS[i] = SUSEUpdate(j)
        elif i.startswith('openSUSE'):
            PROJECT_OPENQA_SETTINGS[i] = openSUSEUpdate(j)
        else:
            raise "Unknown openqa", i

class OpenQABot(ReviewBot.ReviewBot):

    """ check ABI of library packages
    """

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        self.force = False
        self.openqa = None
        self.commentapi = CommentAPI(self.apiurl)
        self.update_test_builds = dict()

    def gather_test_builds(self):
        for prj, u in TARGET_REPO_SETTINGS[self.openqa.baseurl].items():
            buildnr = 0
            cjob = 0
            for j in self.jobs_for_target(u):
                # avoid going backwards in job ID
                if cjob > int(j['id']):
                    continue
                buildnr = j['settings']['BUILD']
                cjob = int(j['id'])
            self.update_test_builds[prj] = buildnr

    # reimplemention from baseclass
    def check_requests(self):

        # first calculate the latest build number for current jobs
        self.gather_test_builds()

        self.pending_target_repos = set()

        started = []
        # then check progress on running incidents
        for req in self.requests:
            # just patch apiurl in to avoid having to pass it around
            jobs = self.request_get_openqa_jobs(req, incident=True, test_repo=True)
            ret = self.calculate_qa_status(jobs)
            if ret != QA_UNKNOWN:
                started.append(req)

        all_requests = self.requests
        self.requests = started
        ReviewBot.ReviewBot.check_requests(self)

        self.requests = all_requests

        skipped_one = False
        # now make sure the jobs are for current repo
        for prj, u in TARGET_REPO_SETTINGS[self.openqa.baseurl].items():
            if prj in self.pending_target_repos:
                skipped_one = True
                continue
            self.trigger_build_for_target(prj, u)

        # do not schedule new incidents unless we finished
        # last wave
        if skipped_one:
            return

        ReviewBot.ReviewBot.check_requests(self)

    def check_action_maintenance_release(self, req, a):
        # we only look at the binaries of the patchinfo
        if a.src_package != 'patchinfo':
            return None

        if a.tgt_project not in PROJECT_OPENQA_SETTINGS:
            self.logger.warn("not handling %s" % a.tgt_project)
            return None

        packages = []
        # patchinfo collects the binaries and is build for an
        # unpredictable architecture so we need iterate over all
        url = osc.core.makeurl(
            self.apiurl,
            ('build', a.src_project, a.tgt_project.replace(':', '_')))
        root = ET.parse(osc.core.http_GET(url)).getroot()
        for arch in [n.attrib['name'] for n in root.findall('entry')]:
            query = {'nosource': 1}
            url = osc.core.makeurl(
                self.apiurl,
                ('build', a.src_project, a.tgt_project.replace(':', '_'), arch, a.src_package),
                query=query)

            root = ET.parse(osc.core.http_GET(url)).getroot()

            for binary in root.findall('binary'):
                m = pkgname_re.match(binary.attrib['filename'])
                if m:
                    # can't use arch here as the patchinfo mixes all
                    # archs
                    packages.append(Package(m.group('name'), m.group('version'), m.group('release')))

        if not packages:
            raise Exception("no packages found")

        update=PROJECT_OPENQA_SETTINGS[a.tgt_project]
        update.apiurl = self.apiurl
        settings = update.settings(a.src_project, a.tgt_project, packages, req)
        if settings:
            # is old style kgraft check if all options correctly set
            if settings['FLAVOR'] == 'KGraft' and 'VIRSH_GUESTNAME' not in settings:
                self.logger.info("build: {!s} hasn't valid values for kgraft".format(settings['BUILD']))
                return None

            # don't start KGRAFT job on Server-DVD-Incidents FLAVOR
            if settings['FLAVOR'] == 'Server-DVD-Incidents':
                if settings['BUILD'].split('.')[1].startswith('kgraft-patch'):
                    return None

            # kernel incidents jobs -- discard all without 'start' = True
            if settings['FLAVOR'] == 'Server-DVD-Incidents-Kernel':
                if 'start' in settings:
                    del settings['start']
                else:
                    return None

            update.calculate_lastest_good_updates(self.openqa, settings)

            self.logger.info("posting %s %s %s", settings['VERSION'], settings['ARCH'], settings['BUILD'])
            self.logger.debug('\n'.join(["  %s=%s" % i for i in settings.items()]))
            if not self.dryrun:
                try:
                    ret = self.openqa.openqa_request('POST', 'isos', data=settings, retries=1)
                    self.logger.info(pformat(ret))
                except JSONDecodeError as e:
                    self.logger.error(e)
                    # TODO: record error
                except openqa_exceptions.RequestError as e:
                    self.logger.error(e)

        return None

    # check a set of repos for their primary checksums
    @staticmethod
    def calculate_repo_hash(repos):
        m = md5.new()
        # if you want to force it, increase this number
        m.update('b')
        for url in repos:
            url += '/repodata/repomd.xml'
            root = ET.parse(osc.core.http_GET(url)).getroot()
            cs = root.find(
                './/{http://linux.duke.edu/metadata/repo}data[@type="primary"]/{http://linux.duke.edu/metadata/repo}checksum')
            m.update(cs.text)
        return m.hexdigest()

    def is_incident_in_testing(self, incident):
        # hard coded for now as we only run this code for SUSE Maintenance workflow
        project = 'SUSE:Maintenance:%s' % incident

        xpath = "(state/@name='review') and (action/source/@project='%s' and action/@type='maintenance_release')" % (project)
        res = osc.core.search(self.apiurl, request=xpath)['request']
        # return the one and only (or None)
        return res.find('request')

    def calculate_incidents(self, incidents):
        """
        get incident numbers from SUSE:Maintenance:Test project
        returns dict with openQA var name : string with numbers
        """
        l_incidents = []
        for kind, prj in incidents.items():
            packages = osc.core.meta_get_packagelist(self.apiurl, prj)
            incidents = []
            # filter out incidents in staging
            for incident in packages:
                # remove patchinfo. prefix
                incident = incident.replace('_', '.').split('.')[1]
                req = self.is_incident_in_testing(incident)
                # without release request it's in staging
                if req is None:
                    continue

                req_ = osc.core.Request()
                req_.read(req)
                kgraft_target, action = SUSEUpdate.kgraft_target(req_)
                # skip kgraft patches from aggregation
                if kgraft_target:
                    continue
                incidents.append(incident)

            l_incidents.append((kind + '_TEST_ISSUES', ','.join(incidents)))

        return l_incidents

    def jobs_for_target(self, data):
        s = data['settings'][0]
        return self.openqa.openqa_request(
            'GET', 'jobs',
            {
                'distri': s['DISTRI'],
                'version': s['VERSION'],
                'arch': s['ARCH'],
                'flavor': s['FLAVOR'],
                'test': data['test'],
                'latest': '1',
            })['jobs']

    # we don't know the current BUILD and querying all jobs is too expensive
    # so we need to check for one known TEST first
    # if that job doesn't contain the proper hash, we trigger a new one
    # and then we know the build
    def trigger_build_for_target(self, prj, data):
        today = date.today().strftime("%Y%m%d")
        repohash = self.calculate_repo_hash(data['repos'])
        buildnr = None
        j = self.jobs_for_target(data)
        for job in j:
            if job['settings'].get('REPOHASH', '') == repohash:
                # take the last in the row
                buildnr = job['settings']['BUILD']
        self.update_test_builds[prj] = buildnr
        # ignore old build numbers, we want a fresh run every day
        # to find regressions in the tests and to get data about
        # randomly failing tests
        if buildnr and buildnr.startswith(today):
            return

        buildnr = 0

        # not found, then check for the next free build nr
        for job in j:
            build = job['settings']['BUILD']
            if build and build.startswith(today):
                try:
                    nr = int(build.split('-')[1])
                    if nr > buildnr:
                        buildnr = nr
                except BaseException:
                    continue

        buildnr = "%s-%d" % (today, buildnr + 1)

        for s in data['settings']:
            # now schedule it for real
            if 'incidents' in data.keys():
                for x, y in self.calculate_incidents(data['incidents']):
                    s[x] = y
            s['BUILD'] = buildnr
            s['REPOHASH'] = repohash
            self.logger.debug(pformat(s))
            if not self.dryrun:
                try:
                    self.openqa.openqa_request('POST', 'isos', data=s, retries=1)
                except Exception as e:
                    self.logger.debug(e)
        self.update_test_builds[prj] = buildnr

    def check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package):
        ReviewBot.ReviewBot.check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package)

    def request_get_openqa_jobs(self, req, incident=True, test_repo=False):
        ret = None
        types = set([a.type for a in req.actions])
        if 'maintenance_release' in types:
            src_prjs = set([a.src_project for a in req.actions])
            if len(src_prjs) != 1:
                raise Exception("can't handle maintenance_release from different incidents")
            build = src_prjs.pop()
            tgt_prjs = set([a.tgt_project for a in req.actions])
            ret = []
            for prj in tgt_prjs:
                if incident and prj in PROJECT_OPENQA_SETTINGS:
                    u=PROJECT_OPENQA_SETTINGS[prj]
                    u.apiurl = self.apiurl
                    s = u.settings(build, prj, [])
                    ret += self.openqa.openqa_request(
                        'GET', 'jobs',
                        {
                            'distri': s['DISTRI'],
                            'version': s['VERSION'],
                            'arch': s['ARCH'],
                            'flavor': s['FLAVOR'],
                            'build': s['BUILD'],
                            'scope': 'relevant',
                        })['jobs']
                repo_settings = TARGET_REPO_SETTINGS.get(self.openqa.baseurl, {})
                if test_repo and prj in repo_settings:
                    u = repo_settings[prj]
                    for s in u['settings']:
                        repo_jobs = self.openqa.openqa_request(
                            'GET', 'jobs',
                            {
                                'distri': s['DISTRI'],
                                'version': s['VERSION'],
                                'arch': s['ARCH'],
                                'flavor': s['FLAVOR'],
                                'build':  self.update_test_builds.get(prj, 'UNKNOWN'),
                                'scope': 'relevant',
                            })['jobs']
                        ret += repo_jobs
                        if self.calculate_qa_status(repo_jobs) == QA_INPROGRESS:
                            self.pending_target_repos.add(prj)
        return ret

    def calculate_qa_status(self, jobs=None):
        if not jobs:
            return QA_UNKNOWN

        j = dict()
        has_failed = False
        in_progress = False
        for job in jobs:
            if job['clone_id']:
                continue
            name = job['name']
            if name in j and int(job['id']) < int(j[name]['id']):
                continue
            j[name] = job
            #self.logger.debug('job %s in openQA: %s %s %s %s', job['id'], job['settings']['VERSION'], job['settings']['TEST'], job['state'], job['result'])
            if job['state'] not in ('cancelled', 'done'):
                in_progress = True
            else:
                if job['result'] != 'passed' and job['result'] != 'softfailed':
                    has_failed = True

        if not j:
            return QA_UNKNOWN
        if in_progress:
            return QA_INPROGRESS
        if has_failed:
            return QA_FAILED

        return QA_PASSED

    def add_comment(self, msg, state, request_id=None, result=None):
        if not self.do_comments:
            return

        comment = "<!-- openqa state=%s%s -->\n" % (state, ' result=%s' % result if result else '')
        comment += "\n" + msg

        info = self.find_obs_request_comment(state=state, request_id=request_id)
        comment_id = info.get('id', None)

        if state == info.get('state', 'missing'):
            lines_before = len(info['comment'].split('\n'))
            lines_after = len(comment.split('\n'))
            if lines_before == lines_after:
                self.logger.debug("not worth the update, previous comment %s is state %s", comment_id, info['state'])
                return

        self.logger.debug("adding comment to %s, state %s result %s", request_id, state, result)
        self.logger.debug("message: %s", msg)
        if not self.dryrun:
            if comment_id is not None:
                self.commentapi.delete(comment_id)
            self.commentapi.add_comment(request_id=request_id, comment=str(comment))

    # escape markdown
    @staticmethod
    def emd(str):
        return str.replace('_', '\_')

    def get_step_url(self, testurl, modulename):
        failurl = testurl + '/modules/%s/fails' % modulename
        fails = requests.get(failurl).json()
        failed_step = fails.get('first_failed_step', 1)
        return "[%s](%s#step/%s/%d)" % (self.emd(modulename), testurl, modulename, failed_step)

    def job_test_name(self, job):
        return "%s@%s" % (self.emd(job['settings']['TEST']), self.emd(job['settings']['MACHINE']))

    def summarize_one_openqa_job(self, job):
        testurl = osc.core.makeurl(self.openqa.baseurl, ['tests', str(job['id'])])
        if not job['result'] in ['passed', 'failed', 'softfailed']:
            rstring = job['result']
            if rstring == 'none':
                return None
            return '\n- [%s](%s) is %s' % (self.job_test_name(job), testurl, rstring)

        modstrings = []
        for module in job['modules']:
            if module['result'] != 'failed':
                continue
            modstrings.append(self.get_step_url(testurl, module['name']))

        if len(modstrings):
            return '\n- [%s](%s) failed in %s' % (self.job_test_name(job), testurl, ','.join(modstrings))
        elif job['result'] == 'failed':  # rare case: fail without module fails
            return '\n- [%s](%s) failed' % (self.job_test_name(job), testurl)
        return ''

    def summarize_openqa_jobs(self, jobs):
        groups = dict()
        for job in jobs:
            gl = "%s@%s" % (self.emd(job['group']), self.emd(job['settings']['FLAVOR']))
            if gl not in groups:
                groupurl = osc.core.makeurl(self.openqa.baseurl, ['tests', 'overview'],
                                            {'version': job['settings']['VERSION'],
                                             'groupid': job['group_id'],
                                             'flavor': job['settings']['FLAVOR'],
                                             'distri': job['settings']['DISTRI'],
                                             'build': job['settings']['BUILD'],
                                             })
                groups[gl] = {'title': "__Group [%s](%s)__\n" % (gl, groupurl),
                              'passed': 0, 'unfinished': 0, 'failed': []}

            job_summary = self.summarize_one_openqa_job(job)
            if job_summary is None:
                groups[gl]['unfinished'] = groups[gl]['unfinished'] + 1
                continue
            # None vs ''
            if not len(job_summary):
                groups[gl]['passed'] = groups[gl]['passed'] + 1
                continue
            # if there is something to report, hold the request
            qa_state = QA_FAILED
            gmsg = groups[gl]
            groups[gl]['failed'].append(job_summary)

        msg = ''
        for group in sorted(groups.keys()):
            msg += "\n\n" + groups[group]['title']
            infos = []
            if groups[group]['passed']:
                infos.append("%d tests passed" % groups[group]['passed'])
            if len(groups[group]['failed']):
                infos.append("%d tests failed" % len(groups[group]['failed']))
            if groups[group]['unfinished']:
                infos.append("%d unfinished tests" % groups[group]['unfinished'])
            msg += "(" + ', '.join(infos) + ")\n"
            for fail in groups[group]['failed']:
                msg += fail

        return msg

    def check_one_request(self, req):
        ret = None

        try:
            jobs = self.request_get_openqa_jobs(req)
            qa_state = self.calculate_qa_status(jobs)
            self.logger.debug("request %s state %s", req.reqid, qa_state)
            msg = None
            if self.force or qa_state == QA_UNKNOWN:
                ret = ReviewBot.ReviewBot.check_one_request(self, req)
                jobs = self.request_get_openqa_jobs(req)

                if self.force:
                    # make sure to delete previous comments if we're forcing
                    info = self.find_obs_request_comment(request_id=req.reqid)
                    if 'id' in info:
                        self.logger.debug("deleting old comment %s", info['id'])
                        if not self.dryrun:
                            self.commentapi.delete(info['id'])

                if not jobs:
                    msg = "no openQA tests defined"
                    self.add_comment(msg, 'done', request_id=req.reqid, result='accepted')
                    ret = True
                else:
                    # no notification until the result is done
                    osc.core.change_review_state(self.apiurl, req.reqid, newstate='new',
                                                 by_group=self.review_group, by_user=self.review_user,
                                                 message='now testing in openQA')
            elif qa_state == QA_FAILED or qa_state == QA_PASSED:
                # don't take test repo results into the calculation of total
                # this is for humans to decide which incident broke the test repo
                jobs += self.request_get_openqa_jobs(req, incident=False, test_repo=True)
                if self.calculate_qa_status(jobs) == QA_INPROGRESS:
                    self.logger.debug(
                        "incident tests for request %s are done, but need to wait for test repo", req.reqid)
                    return
                if qa_state == QA_PASSED:
                    msg = "openQA tests passed\n"
                    result = 'accepted'
                    ret = True
                else:
                    msg = "openQA tests problematic\n"
                    result = 'declined'
                    ret = False

                msg += self.summarize_openqa_jobs(jobs)
                self.add_comment(msg, 'done', result=result, request_id=req.reqid)
            elif qa_state == QA_INPROGRESS:
                self.logger.debug("request %s still in progress", req.reqid)
            else:
                raise Exception("unknown QA state %d", qa_state)

        except Exception:
            import traceback
            self.logger.error("unhandled exception in openQA Bot")
            self.logger.error(traceback.format_exc())
            ret = None

        return ret

    def find_obs_request_comment(self, request_id=None, project_name=None, state=None):
        """Return previous comments (should be one)."""
        if self.do_comments:
            comments = self.commentapi.get_comments(request_id=request_id, project_name=project_name)
            for c in comments.values():
                m = comment_marker_re.match(c['comment'])
                if m and (state is None or state == m.group('state')):
                    return { 'id' : c['id'], 'state': m.group('state'), 'result': m.group('result'), 'comment': c['comment'], 'revision': m.group('revision') }
        return {}

    def check_product(self, job, product_prefix):
        pmap = API_MAP[product_prefix]
        posts = []
        for arch in pmap['archs']:
            need = False
            settings = {'FLAVOR': pmap['flavor'], 'VERSION': pmap['version'], 'ARCH': arch, 'DISTRI': 'sle'}
            issues = pmap.get('issues', {})
            issues['OS_TEST_ISSUES'] = product_prefix
            for key, prefix in issues.items():
                if prefix + arch in job['channels']:
                    settings[key] = str(job['id'])
                    need = True
            if need:
                u = PROJECT_OPENQA_SETTINGS[product_prefix + arch]
                u.apiurl = self.apiurl
                s = u.settings(u.maintenance_project() + ':' + str(job['id']), product_prefix + arch, [])
                if s:
                    if job.get('openqa_build') is None:
                        job['openqa_build'] = u.get_max_revision(job)
                    if job.get('openqa_build') is None:
                        return []
                    s['BUILD'] += '.' + str(job['openqa_build'])
                    s.update(settings)
                    posts.append(s)
        return posts

    def test(self):
        for inc in requests.get('https://maintenance.suse.de/api/incident/active/').json():
            if not inc in ['a4871', 'a5146', 'a2129', '5219', '5217', '5230']: continue
            #if not inc.startswith('52'): continue
            print inc
            #continue
            job = requests.get('https://maintenance.suse.de/api/incident/' + inc).json()
            if job['meta']['state'] in ['final', 'gone']:
                continue
            openqa_posts = []
            for prod in API_MAP.keys():
                s = self.check_product(job['base'], prod)
                openqa_posts += s
            openqa_jobs = []
            openqa_done = True
            for s in openqa_posts:
                jobs = self.openqa.openqa_request(
                        'GET', 'jobs',
                        {
                            'distri': s['DISTRI'],
                            'version': s['VERSION'],
                            'arch': s['ARCH'],
                            'flavor': s['FLAVOR'],
                            'build': s['BUILD'],
                            'scope': 'relevant',
                            'latest': '1'
                        })['jobs']
                if not len(jobs):
                    if self.dryrun:
                        print 'WOULD POST', s
                    else:
                        ret = self.openqa.openqa_request('POST', 'isos', data=s, retries=1)
                    openqa_done = False
                else:
                    print s, 'got', len(jobs)
                    openqa_jobs += jobs
            if not openqa_done or len(openqa_jobs) == 0:
                continue
            #print openqa_jobs
            msg = self.summarize_openqa_jobs(openqa_jobs)
            state = 'seen'
            result = 'none'
            qa_status = self.calculate_qa_status(openqa_jobs)
            if qa_status == QA_PASSED:
                result = 'accepted'
                state = 'done'
            if qa_status == QA_FAILED:
                result = 'declined'
                state = 'done'
            comment = "<!-- openqa state=%s result=%s revision=%s -->\n" % (state, result, job['base'].get('openqa_build'))
            comment += "\n" + msg

            #print comment

            comment_info = self.find_obs_request_comment(state=state, project_name=str(job['base']['project']))
            comment_id = comment_info.get('id', None)
            print "Found comment", comment_id
            if comment_id and state != 'done':
                self.logger.debug("%s is already comented, wait until done", job['base']['project'])
                continue
            if comment_info.get('comment', '') == comment:
                self.logger.debug("%s comment did not change", job['base']['project'])
                continue

            self.logger.debug("adding comment to %s, state %s", job['base']['project'], state)
            #self.logger.debug("message: %s", msg)
            if not self.dryrun:
                if comment_id is not None:
                    self.commentapi.delete(comment_id)
                self.commentapi.add_comment(project_name=str(job['base']['project']), comment=str(comment))


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = OpenQABot

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    def do_test(self, subcmd, opts, *args):
        def work():
            self.checker.test()

        self.runner(work, opts.interval)

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)
        parser.add_option("--force", action="store_true", help="recheck requests that are already considered done")
        parser.add_option("--no-comment", dest='comment', action="store_false",
                          default=True, help="don't actually post comments to obs")
        parser.add_option("--openqa", metavar='HOST', help="openqa api host")
        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.force:
            bot.force = True
        bot.do_comments = self.options.comment
        if not self.options.openqa:
            raise osc.oscerr.WrongArgs("missing openqa url")
        bot.openqa = OpenQA_Client(server=self.options.openqa)

        global logger
        logger = self.logger

        return bot


if __name__ == "__main__":
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.WARNING)
    requests_log.propagate = False

    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
