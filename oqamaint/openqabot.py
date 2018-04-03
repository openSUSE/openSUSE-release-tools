# -*- coding: utf-8 -*-

from collections import namedtuple
from datetime import date
import md5
from pprint import pformat
import re
from urllib2 import HTTPError

import requests
import osc.core

import ReviewBot
from osclib.comments import CommentAPI

from suse import SUSEUpdate

try:
    from xml.etree import cElementTree as ET
except ImportError:
    from xml.etree import ElementTree as ET


try:
    import simplejson as json
except ImportError:
    import json

QA_UNKNOWN = 0
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3

Package = namedtuple('Package', ('name', 'version', 'release'))
pkgname_re = re.compile(r'(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^.]+)\.rpm')
comment_marker_re = re.compile(
    r'<!-- openqa state=(?P<state>done|seen)(?: result=(?P<result>accepted|declined|none))?(?: revision=(?P<revision>\d+))? -->')


class OpenQABot(ReviewBot.ReviewBot):

    """ check ABI of library packages
    """

    def __init__(self, *args, **kwargs):
        super(OpenQABot, self).__init__(*args, **kwargs)
        self.tgt_repo = {}
        self.project_settings = {}
        self.api_map = {}

        self.force = False
        self.openqa = None
        self.commentapi = CommentAPI(self.apiurl)
        self.update_test_builds = {}
        self.pending_target_repos = set()
        self.openqa_jobs = {}

    def gather_test_builds(self):
        for prj, u in self.tgt_repo[self.openqa.baseurl].items():
            buildnr = 0
            cjob = 0
            for j in self.jobs_for_target(u):
                # avoid going backwards in job ID
                if cjob > int(j['id']):
                    continue
                buildnr = j['settings']['BUILD']
                cjob = int(j['id'])
            self.update_test_builds[prj] = buildnr
            jobs = self.jobs_for_target(u, build=buildnr)
            self.openqa_jobs[prj] = jobs
            if self.calculate_qa_status(jobs) == QA_INPROGRESS:
                self.pending_target_repos.add(prj)

    # reimplemention from baseclass
    def check_requests(self):

        if self.ibs:
            self.check_suse_incidents()

        # first calculate the latest build number for current jobs
        self.gather_test_builds()

        started = []
        # then check progress on running incidents
        for req in self.requests:
            jobs = self.request_get_openqa_jobs(req, incident=True, test_repo=True)
            ret = self.calculate_qa_status(jobs)
            if ret != QA_UNKNOWN:
                started.append(req)

        all_requests = self.requests
        self.requests = started
        self.logger.debug("check started requests")
        super(OpenQABot, self).check_requests()

        self.requests = all_requests

        skipped_one = False
        # now make sure the jobs are for current repo
        for prj, u in self.tgt_repo[self.openqa.baseurl].items():
            if prj in self.pending_target_repos:
                skipped_one = True
                continue
            self.trigger_build_for_target(prj, u)

        # do not schedule new incidents unless we finished
        # last wave
        if skipped_one:
            return
        self.logger.debug("Check all requests")
        super(OpenQABot, self).check_requests()

    # check a set of repos for their primary checksums
    @staticmethod
    def calculate_repo_hash(repos):
        m = md5.new()
        # if you want to force it, increase this number
        m.update('b')
        for url in repos:
            url += '/repodata/repomd.xml'
            try:
                root = ET.parse(osc.core.http_GET(url)).getroot()
            except HTTPError:
                raise
            cs = root.find(
                './/{http://linux.duke.edu/metadata/repo}data[@type="primary"]/{http://linux.duke.edu/metadata/repo}checksum')
            m.update(cs.text)
        return m.hexdigest()

    def is_incident_in_testing(self, incident):
        # hard coded for now as we only run this code for SUSE Maintenance workflow
        project = 'SUSE:Maintenance:{}'.format(incident)

        xpath = "(state/@name='review') and (action/source/@project='{}' and action/@type='maintenance_release')".format(project)
        res = osc.core.search(self.apiurl, request=xpath)['request']
        # return the one and only (or None)
        return res.find('request')

    def calculate_incidents(self, incidents):
        """
        get incident numbers from SUSE:Maintenance:Test project
        returns dict with openQA var name : string with numbers
        """
        self.logger.debug("calculate_incidents: {}".format(pformat(incidents)))
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
                if not req:
                    continue

                # skip kgraft patches from aggregation
                req_ = osc.core.Request()
                req_.read(req)
                src_prjs = {a.src_project for a in req_.actions}
                if SUSEUpdate.kgraft_target(self.apiurl, src_prjs.pop()):
                    self.logger.debug("calculate_incidents: Incident is kgraft - {} ".format(incident))
                    continue

                incidents.append(incident)

            l_incidents.append((kind + '_TEST_ISSUES', ','.join(incidents)))
        self.logger.debug("Calculate incidents:{}".format(pformat(l_incidents)))
        return l_incidents

    def jobs_for_target(self, data, build=None):
        settings = data['settings'][0]
        values = {
            'distri': settings['DISTRI'],
            'version': settings['VERSION'],
            'arch': settings['ARCH'],
            'flavor': settings['FLAVOR'],
            'scope': 'relevant',
            'latest': '1',
        }
        if build:
            values['build'] = build
        else:
            values['test'] = data['test']
        self.logger.debug("Get jobs: {}".format(pformat(values)))
        return self.openqa.openqa_request('GET', 'jobs', values)['jobs']

    # we don't know the current BUILD and querying all jobs is too expensive
    # so we need to check for one known TEST first
    # if that job doesn't contain the proper hash, we trigger a new one
    # and then we know the build
    def trigger_build_for_target(self, prj, data):
        today = date.today().strftime("%Y%m%d")

        try:
            repohash = self.calculate_repo_hash(data['repos'])
        except HTTPError as e:
            self.logger.debug("REPOHAS not calculated with response {}".format(e))
            return

        buildnr = None
        jobs = self.jobs_for_target(data)
        for job in jobs:
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
        for job in jobs:
            build = job['settings']['BUILD']
            if build and build.startswith(today):
                try:
                    nr = int(build.split('-')[1])
                    if nr > buildnr:
                        buildnr = nr
                except ValueError:
                    continue

        buildnr = "{!s}-{:d}".format(today, buildnr + 1)

        for s in data['settings']:
            # now schedule it for real
            if 'incidents' in data.keys():
                for x, y in self.calculate_incidents(data['incidents']):
                    s[x] = y
            s['BUILD'] = buildnr
            s['REPOHASH'] = repohash
            self.logger.debug("Prepared: {}".format(pformat(s)))
            if not self.dryrun:
                try:
                    self.logger.info("Openqa isos POST {}".format(pformat(s)))
                    self.openqa.openqa_request('POST', 'isos', data=s, retries=1)
                except Exception as e:
                    self.logger.error(e)
        self.update_test_builds[prj] = buildnr

    def request_get_openqa_jobs(self, req, incident=True, test_repo=False):
        ret = None
        types = {a.type for a in req.actions}
        if 'maintenance_release' in types:
            src_prjs = {a.src_project for a in req.actions}
            if len(src_prjs) != 1:
                raise Exception("can't handle maintenance_release from different incidents")
            build = src_prjs.pop()
            tgt_prjs = {a.tgt_project for a in req.actions}
            ret = []
            if incident:
                ret += self.openqa_jobs[build]
            for prj in sorted(tgt_prjs):
                repo_settings = self.tgt_repo.get(self.openqa.baseurl, {})
                if test_repo and prj in repo_settings:
                    repo_jobs = self.openqa_jobs[prj]
                    ret += repo_jobs

        return ret

    def calculate_qa_status(self, jobs=None):
        if not jobs:
            return QA_UNKNOWN

        j = {}
        has_failed = False
        in_progress = False

        for job in jobs:
            if job['clone_id']:
                continue
            name = job['name']

            if name in j and int(job['id']) < int(j[name]['id']):
                continue
            j[name] = job

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

        comment = "<!-- openqa state={!s}{!s} -->\n".format(state, ' result={!s}'.format(result) if result else '')
        comment += "\n" + msg

        info = self.find_obs_request_comment(request_id=request_id)
        comment_id = info.get('id', None)

        if state == info.get('state', 'missing'):
            lines_before = len(info['comment'].split('\n'))
            lines_after = len(comment.split('\n'))
            if lines_before == lines_after:
                self.logger.info("not worth the update, previous comment %s is state %s", comment_id, info['state'])
                return

        self.logger.info("adding comment to %s, state %s result %s", request_id, state, result)
        self.logger.info("message: %s", msg)
        if not self.dryrun:
            if comment_id:
                self.commentapi.delete(comment_id)
            self.commentapi.add_comment(request_id=request_id, comment=str(comment))

    # escape markdown
    @staticmethod
    def emd(str):
        return str.replace('_', r'\_')

    @staticmethod
    def get_step_url(testurl, modulename):
        failurl = testurl + '/modules/{!s}/fails'.format(modulename)
        fails = requests.get(failurl).json()
        failed_step = fails.get('first_failed_step', 1)
        return "[{!s}]({!s}#step/{!s}/{:d})".format(OpenQABot.emd(modulename), testurl, modulename, failed_step)

    @staticmethod
    def job_test_name(job):
        return "{!s}@{!s}".format(OpenQABot.emd(job['settings']['TEST']), OpenQABot.emd(job['settings']['MACHINE']))

    def summarize_one_openqa_job(self, job):
        testurl = osc.core.makeurl(self.openqa.baseurl, ['tests', str(job['id'])])
        if not job['result'] in ['passed', 'failed', 'softfailed']:
            rstring = job['result']
            if rstring == 'none':
                return None
            return '\n- [{!s}]({!s}) is {!s}'.format(self.job_test_name(job), testurl, rstring)

        modstrings = []
        for module in job['modules']:
            if module['result'] != 'failed':
                continue
            modstrings.append(self.get_step_url(testurl, module['name']))

        if modstrings:
            return '\n- [{!s}]({!s}) failed in {!s}'.format(self.job_test_name(job), testurl, ','.join(modstrings))
        elif job['result'] == 'failed':  # rare case: fail without module fails
            return '\n- [{!s}]({!s}) failed'.format(self.job_test_name(job), testurl)
        return ''

    def summarize_openqa_jobs(self, jobs):
        groups = {}
        for job in jobs:
            gl = "{!s}@{!s}".format(self.emd(job['group']), self.emd(job['settings']['FLAVOR']))
            if gl not in groups:
                groupurl = osc.core.makeurl(self.openqa.baseurl, ['tests', 'overview'],
                                            {'version': job['settings']['VERSION'],
                                             'groupid': job['group_id'],
                                             'flavor': job['settings']['FLAVOR'],
                                             'distri': job['settings']['DISTRI'],
                                             'build': job['settings']['BUILD'],
                                             })
                groups[gl] = {'title': "__Group [{!s}]({!s})__\n".format(gl, groupurl),
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
            # TODO: what is this ?
            # qa_state = QA_FAILED
            # gmsg = groups[gl]

            groups[gl]['failed'].append(job_summary)

        msg = ''
        for group in sorted(groups.keys()):
            msg += "\n\n" + groups[group]['title']
            infos = []
            if groups[group]['passed']:
                infos.append("{:d} tests passed".format(groups[group]['passed']))
            if len(groups[group]['failed']):
                infos.append("{:d} tests failed".format(len(groups[group]['failed'])))
            if groups[group]['unfinished']:
                infos.append("{:d} unfinished tests".format(groups[group]['unfinished']))
            msg += "(" + ', '.join(infos) + ")\n"
            for fail in groups[group]['failed']:
                msg += fail
        return msg.rstrip('\n')

    def check_one_request(self, req):
        ret = None

        try:
            jobs = self.request_get_openqa_jobs(req)
            qa_state = self.calculate_qa_status(jobs)
            self.logger.debug("request %s state %s", req.reqid, qa_state)
            msg = None
            if self.force or qa_state == QA_UNKNOWN:
                ret = super(OpenQABot, self).check_one_request(req)
                jobs = self.request_get_openqa_jobs(req)

                if self.force:
                    # make sure to delete previous comments if we're forcing
                    info = self.find_obs_request_comment(request_id=req.reqid)
                    if 'id' in info:
                        self.logger.debug("deleting old comment %s", info['id'])
                        if not self.dryrun:
                            self.commentapi.delete(info['id'])

                if jobs:
                    # no notification until the result is done
                    osc.core.change_review_state(self.apiurl, req.reqid, newstate='new',
                                                 by_group=self.review_group, by_user=self.review_user,
                                                 message='now testing in openQA')
                else:
                    msg = "no openQA tests defined"
                    self.add_comment(msg, 'done', request_id=req.reqid, result='accepted')
                    ret = True
            elif qa_state == QA_FAILED or qa_state == QA_PASSED:
                # don't take test repo results into the calculation of total
                # this is for humans to decide which incident broke the test repo
                jobs += self.request_get_openqa_jobs(req, incident=False, test_repo=True)
                if self.calculate_qa_status(jobs) == QA_INPROGRESS:
                    self.logger.info(
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
                self.logger.info("request %s still in progress", req.reqid)
            else:
                raise Exception("unknown QA state %d", qa_state)

        except Exception:
            import traceback
            self.logger.error("unhandled exception in openQA Bot")
            self.logger.error(traceback.format_exc())
            ret = None

        return ret

    def find_obs_request_comment(self, request_id=None, project_name=None):
        """Return previous comments (should be one)."""
        if self.do_comments:
            comments = self.commentapi.get_comments(request_id=request_id, project_name=project_name)
            for c in comments.values():
                m = comment_marker_re.match(c['comment'])
                if m:
                    return {
                        'id': c['id'],
                        'state': m.group('state'),
                        'result': m.group('result'),
                        'comment': c['comment'],
                        'revision': m.group('revision')}
        return {}

    def check_product(self, job, product_prefix):
        pmap = self.api_map[product_prefix]
        posts = []
        for arch in pmap['archs']:
            need = False
            settings = {'VERSION': pmap['version'], 'ARCH': arch}
            settings['DISTRI'] = 'sle' if 'distri' not in pmap else pmap['distri']
            issues = pmap.get('issues', {})
            issues['OS_TEST_ISSUES'] = issues.get('OS_TEST_ISSUES', product_prefix)
            required_issue = pmap.get('required_issue', False)
            for key, prefix in issues.items():
                self.logger.debug("{} {}".format(key, prefix))
                if prefix + arch in job['channels']:
                    settings[key] = str(job['id'])
                    need = True
            if required_issue:
                if required_issue not in settings:
                    need = False

            if need:
                update = self.project_settings[product_prefix + arch]
                update.apiurl = self.apiurl
                update.logger = self.logger
                for j in update.settings(
                        update.maintenance_project + ':' + str(job['id']),
                        product_prefix + arch, []):
                    if not job.get('openqa_build'):
                        job['openqa_build'] = update.get_max_revision(job)
                    if not job.get('openqa_build'):
                        return []
                    j['BUILD'] += '.' + str(job['openqa_build'])
                    j.update(settings)
                    # kGraft jobs can have different version
                    if 'real_version' in j:
                        j['VERSION'] = j['real_version']
                        del j['real_version']
                    posts.append(j)
        self.logger.debug("Pmap: {} Posts: {}".format(pmap, posts))
        return posts

    def incident_openqa_jobs(self, s):
        return self.openqa.openqa_request(
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

    def check_suse_incidents(self):
        for inc in requests.get('https://maintenance.suse.de/api/incident/active/').json():
            self.logger.info("Incident number: {}".format(inc))

            job = requests.get('https://maintenance.suse.de/api/incident/' + inc).json()

            if job['meta']['state'] in ['final', 'gone']:
                continue
            # required in job: project, id, channels
            self.test_job(job['base'])

    def test_job(self, job):
        self.logger.debug("Called test_job with: {}".format(job))
        incident_project = str(job['project'])
        try:
            comment_info = self.find_obs_request_comment(project_name=incident_project)
        except HTTPError as e:
            self.logger.debug("Couldn't loaadd comments - {}".format(e))
            return
        comment_id = comment_info.get('id', None)
        comment_build = str(comment_info.get('revision', ''))

        openqa_posts = []
        for prod in self.api_map.keys():
            self.logger.debug("{} -- product in apimap".format(prod))
            openqa_posts += self.check_product(job, prod)
        openqa_jobs = []
        for s in openqa_posts:
            jobs = self.incident_openqa_jobs(s)
            # take the project comment as marker for not posting jobs
            if not len(jobs) and comment_build != str(job['openqa_build']):
                if self.dryrun:
                    self.logger.info('WOULD POST:{}'.format(pformat(json.dumps(s, sort_keys=True))))
                else:
                    self.logger.info("Posted: {}".format(pformat(json.dumps(s, sort_keys=True))))
                    self.openqa.openqa_request('POST', 'isos', data=s, retries=1)
                    openqa_jobs += self.incident_openqa_jobs(s)
            else:
                self.logger.info("{} got {}".format(pformat(s), len(jobs)))
                openqa_jobs += jobs

        self.openqa_jobs[incident_project] = openqa_jobs

        if len(openqa_jobs) == 0:
            self.logger.debug("No openqa jobs defined")
            return
        # print openqa_jobs
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
        comment = "<!-- openqa state={!s} result={!s} revision={!s} -->\n".format(
            state, result, job.get('openqa_build'))
        comment += msg

        if comment_id and state != 'done':
            self.logger.info("%s is already commented, wait until done", incident_project)
            return
        if comment_info.get('comment', '').rstrip('\n') == comment.rstrip('\n'):
            self.logger.info("%s comment did not change", incident_project)
            return

        self.logger.info("adding comment to %s, state %s", incident_project, state)
        if not self.dryrun:
            if comment_id:
                self.logger.debug("delete comment: {}".format(comment_id))
                self.commentapi.delete(comment_id)
            self.commentapi.add_comment(project_name=str(incident_project), comment=str(comment))
