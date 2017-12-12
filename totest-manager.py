#!/usr/bin/python2
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import cmdln
import datetime
import json
import os
import re
import sys
import urllib2
import logging
import signal
import time

from xml.etree import cElementTree as ET
from openqa_client.client import OpenQA_Client

import osc

from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osc.core import makeurl

logger = logging.getLogger()

ISSUE_FILE = 'issues_to_ignore'

# QA Results
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3


class NotFoundException(Exception):
    pass


class ToTestBase(object):

    """Base class to store the basic interface"""

    def __init__(self, project, dryrun=False, api_url=None, openqa_server='https://openqa.opensuse.org', test_subproject=None):
        self.project = project
        self.dryrun = dryrun
        if not api_url:
            api_url = osc.conf.config['apiurl']
        self.api = StagingAPI(api_url, project=project)
        self.openqa_server = openqa_server
        if not test_subproject:
            test_subproject = 'ToTest'
        self.test_project = '%s:%s' % (self.project, test_subproject)
        self.openqa = OpenQA_Client(server=openqa_server)
        self.issues_to_ignore = []
        self.issuefile = "{}_{}".format(self.project, ISSUE_FILE)
        if os.path.isfile(self.issuefile):
            with open(self.issuefile, 'r') as f:
                for line in f.readlines():
                    self.issues_to_ignore.append(line.strip())
        self.project_base = project.split(':')[0]
        self.update_pinned_descr = False

    def openqa_group(self):
        return self.project

    def iso_prefix(self):
        return self.project

    def jobs_num(self):
        return 70

    def current_version(self):
        return self.release_version()

    def binaries_of_product(self, project, product):
        url = self.api.makeurl(['build', project, 'images', 'local', product])
        try:
            f = self.api.retried_GET(url)
        except urllib2.HTTPError:
            return []

        ret = []
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            ret.append(binary.get('filename'))

        return ret

    def get_current_snapshot(self):
        """Return the current snapshot in the test project"""

        for binary in self.binaries_of_product(self.test_project, '_product:%s-cd-mini-%s' % (self.project_base, self.arch())):
            result = re.match(r'%s-%s-NET-.*-Snapshot(.*)-Media.iso' % (self.project_base, self.iso_prefix()),
                              binary)
            if result:
                return result.group(1)

        return None

    def ftp_build_version(self, project, tree, base=None):
        if not base:
            base = self.project_base
        for binary in self.binaries_of_product(project, tree):
            result = re.match(r'%s.*Build(.*)-Media1.report' % base, binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s ftp version" % project)

    def iso_build_version(self, project, tree, base=None):
        if not base:
            base = self.project_base
        for binary in self.binaries_of_product(project, tree):
            result = re.match(r'%s.*Build(.*)-Media(.*).iso' % base, binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s iso version" % project)

    def release_version(self):
        url = self.api.makeurl(['build', self.project, 'standard', self.arch(),
                                '_product:%s-release' % self.project_base])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)

        raise NotFoundException("can't find %s version" % self.project)

    def find_openqa_results(self, snapshot):
        """Return the openqa jobs of a given snapshot and filter out the
        cloned jobs

        """

        url = makeurl(self.openqa_server,
                      ['api', 'v1', 'jobs'], {'group': self.openqa_group(), 'build': snapshot, 'latest': 1})
        f = self.api.retried_GET(url)
        jobs = []
        for job in json.load(f)['jobs']:
            if job['clone_id'] or job['result'] == 'obsoleted':
                continue
            job['name'] = job['name'].replace(snapshot, '')
            jobs.append(job)
        return jobs

    def _result2str(self, result):
        if result == QA_INPROGRESS:
            return 'inprogress'
        elif result == QA_FAILED:
            return 'failed'
        else:
            return 'passed'

    def find_failed_module(self, testmodules):
        # print json.dumps(testmodules, sort_keys=True, indent=4)
        for module in testmodules:
            if module['result'] != 'failed':
                continue
            flags = module['flags']
            if 'fatal' in flags or 'important' in flags:
                return module['name']
                break
            logger.info('%s %s %s' %
                        (module['name'], module['result'], module['flags']))

    def update_openqa_status_message(self):
        url = makeurl(self.openqa_server,
                      ['api', 'v1', 'job_groups'])
        f = self.api.retried_GET(url)
        job_groups = json.load(f)
        group_id = 0
        for jg in job_groups:
            if jg['name'] == self.openqa_group():
                group_id = jg['id']
                break

        if not group_id:
            logger.debug('No openQA group id found for status comment update, ignoring')
            return

        pinned_ignored_issue = 0
        issues = ' , '.join(self.issues_to_ignore)
        status_flag = 'publishing' if self.status_for_openqa['is_publishing'] else \
            'preparing' if self.status_for_openqa['can_release'] else \
                'testing' if self.status_for_openqa['snapshotable'] else \
                'building'
        status_msg = "tag:{}:{}:{}".format(self.status_for_openqa['new_snapshot'], status_flag, status_flag)
        msg = "pinned-description: Ignored issues\r\n\r\n{}\r\n\r\n{}".format(issues, status_msg)
        data = {'text': msg}

        url = makeurl(self.openqa_server,
                      ['api', 'v1', 'groups', str(group_id), 'comments'])
        f = self.api.retried_GET(url)
        comments = json.load(f)
        for comment in comments:
            if comment['userName'] == 'ttm' and \
                    comment['text'].startswith('pinned-description: Ignored issues'):
                pinned_ignored_issue = comment['id']

        logger.debug('Writing openQA status message: {}'.format(data))
        if not self.dryrun:
            if pinned_ignored_issue:
                self.openqa.openqa_request(
                    'PUT', 'groups/%s/comments/%d' % (group_id, pinned_ignored_issue), data=data)
            else:
                self.openqa.openqa_request(
                    'POST', 'groups/%s/comments' % group_id, data=data)

    def overall_result(self, snapshot):
        """Analyze the openQA jobs of a given snapshot Returns a QAResult"""

        if snapshot is None:
            return QA_FAILED

        jobs = self.find_openqa_results(snapshot)

        if len(jobs) < self.jobs_num():  # not yet scheduled
            logger.warning('we have only %s jobs' % len(jobs))
            return QA_INPROGRESS

        number_of_fails = 0
        in_progress = False
        for job in jobs:
            # print json.dumps(job, sort_keys=True, indent=4)
            if job['result'] in ('failed', 'incomplete', 'skipped', 'user_cancelled', 'obsoleted', 'parallel_failed'):
                jobname = job['name']
                # print json.dumps(job, sort_keys=True, indent=4), jobname
                url = makeurl(self.openqa_server,
                              ['api', 'v1', 'jobs', str(job['id']), 'comments'])
                f = self.api.retried_GET(url)
                comments = json.load(f)
                refs = set()
                labeled = 0
                to_ignore = False
                for comment in comments:
                    for ref in comment['bugrefs']:
                        refs.add(str(ref))
                    if comment['userName'] == 'ttm' and comment['text'] == 'label:unknown_failure':
                        labeled = comment['id']
                    if comment['text'].find('@ttm ignore') >= 0:
                        to_ignore = True
                ignored = len(refs) > 0
                for ref in refs:
                    if ref not in self.issues_to_ignore:
                        if to_ignore:
                            self.issues_to_ignore.append(ref)
                            self.update_pinned_descr = True
                            with open(self.issuefile, 'a') as f:
                                f.write("%s\n" % ref)
                        else:
                            ignored = False

                if not ignored:
                    number_of_fails += 1
                    if not labeled and len(refs) > 0 and not self.dryrun:
                        data = {'text': 'label:unknown_failure'}
                        self.openqa.openqa_request(
                            'POST', 'jobs/%s/comments' % job['id'], data=data)
                elif labeled:
                    # remove flag - unfortunately can't delete comment unless admin
                    data = {'text': 'Ignored issue'}
                    self.openqa.openqa_request(
                        'PUT', 'jobs/%s/comments/%d' % (job['id'], labeled), data=data)

                if ignored:
                    logger.info("job %s failed, but was ignored", jobname)
                else:
                    joburl = '%s/tests/%s' % (self.openqa_server, job['id'])
                    logger.info("job %s failed, see %s", jobname, joburl)

            elif job['result'] == 'passed' or job['result'] == 'softfailed':
                continue
            elif job['result'] == 'none':
                if job['state'] != 'cancelled':
                    in_progress = True
            else:
                raise Exception(job['result'])

        if number_of_fails > 0:
            return QA_FAILED

        if in_progress:
            return QA_INPROGRESS

        return QA_PASSED

    def all_repos_done(self, project, codes=None):
        """Check the build result of the project and only return True if all
        repos of that project are either published or unpublished

        """

        # coolo's experience says that 'finished' won't be
        # sufficient here, so don't try to add it :-)
        codes = ['published', 'unpublished'] if not codes else codes

        url = self.api.makeurl(
            ['build', project, '_result'], {'code': 'failed'})
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        ready = True
        for repo in root.findall('result'):
            # ignore ports. 'factory' is used by arm for repos that are not
            # meant to use the totest manager.
            if repo.get('repository') in ('ports', 'factory', 'images_staging'):
                continue
            # ignore 32bit for now. We're only interesed in aarch64 here
            if repo.get('arch') in ('armv6l', 'armv7l'):
                continue
            if repo.get('dirty', '') == 'true':
                logger.info('%s %s %s -> %s' % (repo.get('project'),
                                                repo.get('repository'), repo.get('arch'), 'dirty'))
                ready = False
            if repo.get('code') not in codes:
                logger.info('%s %s %s -> %s' % (repo.get('project'),
                                                repo.get('repository'), repo.get('arch'), repo.get('code')))
                ready = False
        return ready

    def maxsize_for_package(self, package):
        if re.match(r'.*-mini-.*', package):
            return 737280000  # a CD needs to match

        if re.match(r'.*-dvd5-.*', package):
            return 4700372992  # a DVD needs to match

        if re.match(r'livecd-x11', package):
            return 681574400  # not a full CD

        if re.match(r'livecd-.*', package):
            return 999999999  # a GB stick

        if re.match(r'.*-(dvd9-dvd|cd-DVD)-.*', package):
            return 8539996159

        if re.match(r'.*-ftp-(ftp|POOL)-', package):
            return None

        if ':%s-Addon-NonOss-ftp-ftp' % self.base in package:
            return None

        raise Exception('No maxsize for {}'.format(package))

    def package_ok(self, project, package, repository, arch):
        """Checks one package in a project and returns True if it's succeeded

        """

        query = {'package': package, 'repository': repository, 'arch': arch}

        url = self.api.makeurl(['build', project, '_result'], query)
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for repo in root.findall('result'):
            status = repo.find('status')
            if status.get('code') != 'succeeded':
                logger.info(
                    '%s %s %s %s -> %s' % (project, package, repository, arch, status.get('code')))
                return False

        maxsize = self.maxsize_for_package(package)
        if not maxsize:
            return True

        url = self.api.makeurl(['build', project, repository, arch, package])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            if not binary.get('filename', '').endswith('.iso'):
                continue
            isosize = int(binary.get('size', 0))
            if isosize > maxsize:
                logger.error('%s %s %s %s: %s' % (
                    project, package, repository, arch, 'too large by %s bytes' % (isosize - maxsize)))
                return False

        return True

    def is_snapshottable(self):
        """Check various conditions required for factory to be snapshotable

        """

        if not self.all_repos_done(self.project):
            return False

        for product in self.ftp_products + self.main_products:
            if not self.package_ok(self.project, product, 'images', 'local'):
                return False

            if len(self.livecd_products):

                if not self.all_repos_done('%s:Live' % self.project):
                    return False

                for arch in ['i586', 'x86_64']:
                    for product in self.livecd_products:
                        if not self.package_ok('%s:Live' % self.project, product, 'images', arch):
                            return False

        return True

    def _release_package(self, project, package, set_release=None):
        query = {'cmd': 'release'}

        if set_release:
            query['setrelease'] = set_release

        # FIXME: make configurable. openSUSE:Factory:ARM currently has multiple
        # repos with release targets, so obs needs to know which one to release
        if project == 'openSUSE:Factory:ARM':
            query['repository'] = 'images'

        baseurl = ['source', project, package]

        url = self.api.makeurl(baseurl, query=query)
        if self.dryrun:
            logger.info("release %s/%s (%s)" % (project, package, set_release))
        else:
            self.api.retried_POST(url)

    def _release(self, set_release=None):
        for product in self.ftp_products:
            self._release_package(self.project, product)

        for cd in self.livecd_products:
            self._release_package('%s:Live' %
                                  self.project, cd, set_release=set_release)

        for cd in self.main_products:
            self._release_package(self.project, cd, set_release=set_release)

    def update_totest(self, snapshot=None):
        release = 'Snapshot%s' % snapshot if snapshot else None
        logger.info('Updating snapshot %s' % snapshot)
        if not self.dryrun:
            self.api.switch_flag_in_prj(self.test_project, flag='publish', state='disable')

        self._release(set_release=release)

    def publish_factory_totest(self):
        logger.info('Publish test project content')
        if not self.dryrun:
            self.api.switch_flag_in_prj(
                self.test_project, flag='publish', state='enable')

    def totest_is_publishing(self):
        """Find out if the publishing flag is set in totest's _meta"""

        url = self.api.makeurl(
            ['source', self.test_project, '_meta'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        if not root.find('publish'):  # default true
            return True

        for flag in root.find('publish'):
            if flag.get('repository', None) or flag.get('arch', None):
                continue
            if flag.tag == 'enable':
                return True
        return False

    def totest(self):
        try:
            current_snapshot = self.get_current_snapshot()
        except NotFoundException as e:
            # nothing in test project (yet)
            logger.warn(e)
            current_snapshot = None
        new_snapshot = self.current_version()
        self.update_pinned_descr = False
        current_result = self.overall_result(current_snapshot)
        current_qa_version = self.api.dashboard_content_load('version_totest')

        logger.info('current_snapshot %s: %s' %
                    (current_snapshot, self._result2str(current_result)))
        logger.debug('new_snapshot %s', new_snapshot)
        logger.debug('current_qa_version %s', current_qa_version)

        snapshotable = self.is_snapshottable()
        logger.debug("snapshotable: %s", snapshotable)
        can_release = (current_result != QA_INPROGRESS and snapshotable)

        # not overwriting
        if new_snapshot == current_snapshot:
            logger.debug("no change in snapshot version")
            can_release = False
        elif not self.all_repos_done(self.test_project):
            logger.debug("not all repos done, can't release")
            # the repos have to be done, otherwise we better not touch them
            # with a new release
            can_release = False

        can_publish = (current_result == QA_PASSED)

        # already published
        totest_is_publishing = self.totest_is_publishing()
        if totest_is_publishing:
            logger.debug("totest already publishing")
            can_publish = False

        if self.update_pinned_descr:
            self.status_for_openqa = {
                'current_snapshot': current_snapshot,
                'new_snapshot': new_snapshot,
                'snapshotable': snapshotable,
                'can_release': can_release,
                'is_publishing': totest_is_publishing,
            }
            self.update_openqa_status_message()

        if can_publish:
            if current_qa_version == current_snapshot:
                self.publish_factory_totest()
                self.write_version_to_dashboard("snapshot", current_snapshot)
                can_release = False  # we have to wait
            else:
                # We reached a very bad status: openQA testing is 'done', but not of the same version
                # currently in test project. This can happen when 'releasing' the
                # product failed
                raise Exception("Publishing stopped: tested version (%s) does not match version in test project (%s)"
                                % (current_qa_version, current_snapshot))

        if can_release:
            self.update_totest(new_snapshot)
            self.write_version_to_dashboard("totest", new_snapshot)

    def release(self):
        new_snapshot = self.current_version()
        self.update_totest(new_snapshot)

    def write_version_to_dashboard(self, target, version):
        if not self.dryrun:
            url = self.api.makeurl(
                ['source', '%s:Staging' % self.project, 'dashboard', 'version_%s' % target])
            osc.core.http_PUT(url + '?comment=Update+version', data=version)


class ToTestBaseNew(ToTestBase):

    """Base class for new product builder"""
    def _release(self, set_release=None):
        query = {'cmd': 'release'}

        package = '000product'
        project = self.project

        if set_release:
            query['setrelease'] = set_release

        baseurl = ['source', project, package]

        url = self.api.makeurl(baseurl, query=query)
        if self.dryrun:
            logger.info("release %s/%s (%s)" % (project, package, set_release))
        else:
            self.api.retried_POST(url)

        # XXX still legacy
        for cd in self.livecd_products:
            self._release_package('%s:Live' %
                                  self.project, cd, set_release=set_release)

    def release_version(self):
        url = self.api.makeurl(['build', self.project, 'standard', self.arch(),
                                '000product:%s-release' % self.project_base])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)

        raise NotFoundException("can't find %s release version" % self.project)

    def current_version(self):
        return self.iso_build_version(self.project, self.main_products[0])

    def is_snapshottable(self):
        ret = super(ToTestBaseNew, self).is_snapshottable()
        if ret:
            # make sure all medias have the same build number
            builds = set()
            for p in self.ftp_products:
                if 'Addon-NonOss' in p:
                    # XXX: don't care about nonoss atm.
                    continue
                builds.add(self.ftp_build_version(self.project, p))
            for p in self.main_products + self.livecd_products:
                builds.add(self.iso_build_version(self.project, p))

            ret = (len(builds) == 1)

        return ret

    def update_totest(self, snapshot):
        # omit snapshot, we don't want to rename on release
        super(ToTestBaseNew, self).update_totest()


class ToTestFactory(ToTestBase):
    main_products = ['_product:openSUSE-dvd5-dvd-i586',
                     '_product:openSUSE-dvd5-dvd-x86_64',
                     '_product:openSUSE-cd-mini-i586',
                     '_product:openSUSE-cd-mini-x86_64',
                     '_product:openSUSE-Tumbleweed-Kubic-dvd5-dvd-x86_64']

    ftp_products = ['_product:openSUSE-ftp-ftp-i586_x86_64',
                    '_product:openSUSE-Addon-NonOss-ftp-ftp-i586_x86_64']

    livecd_products = ['livecd-kde',
                       'livecd-gnome',
                       'livecd-x11']

    def __init__(self, *args, **kwargs):
        ToTestBase.__init__(self, *args, **kwargs)

    def openqa_group(self):
        return 'openSUSE Tumbleweed'

    def iso_prefix(self):
        return 'Tumbleweed'

    def arch(self):
        return 'x86_64'


class ToTestFactoryPowerPC(ToTestBase):
    main_products = ['_product:openSUSE-dvd5-dvd-ppc64',
                     '_product:openSUSE-dvd5-dvd-ppc64le',
                     '_product:openSUSE-cd-mini-ppc64',
                     '_product:openSUSE-cd-mini-ppc64le']

    ftp_products = ['_product:openSUSE-ftp-ftp-ppc64_ppc64le']

    livecd_products = []

    def __init__(self, *args, **kwargs):
        ToTestBase.__init__(self, *args, **kwargs)

    def openqa_group(self):
        return 'openSUSE Tumbleweed PowerPC'

    def arch(self):
        return 'ppc64le'

    def iso_prefix(self):
        return 'Tumbleweed'

    def jobs_num(self):
        return 4


class ToTestFactoryzSystems(ToTestBase):
    main_products = ['000product:openSUSE-dvd5-dvd-s390x',
                     '000product:openSUSE-cd-mini-s390x']

    ftp_products = ['000product:openSUSE-ftp-ftp-s390x']

    livecd_products = []

    def __init__(self, *args, **kwargs):
        ToTestBase.__init__(self, *args, **kwargs)

    def openqa_group(self):
        return 'openSUSE Tumbleweed s390x'

    def arch(self):
        return 's390x'

    def iso_prefix(self):
        return 'Tumbleweed'

    def jobs_num(self):
        return 1


class ToTestFactoryARM(ToTestFactory):
    main_products = ['_product:openSUSE-cd-mini-aarch64',
                     '_product:openSUSE-dvd5-dvd-aarch64']

    ftp_products = ['_product:openSUSE-ftp-ftp-aarch64']

    livecd_products = []

    def __init__(self, *args, **kwargs):
        ToTestFactory.__init__(self, *args, **kwargs)

    def openqa_group(self):
        return 'openSUSE Tumbleweed AArch64'

    def arch(self):
        return 'aarch64'

    def jobs_num(self):
        return 2


class ToTest150(ToTestBaseNew):
    main_products = [
        '000product:openSUSE-cd-mini-x86_64',
        '000product:openSUSE-dvd5-dvd-x86_64',
    ]

    ftp_products = ['000product:openSUSE-ftp-ftp-x86_64',
                    '000product:openSUSE-Addon-NonOss-ftp-ftp-x86_64'
                    ]

    livecd_products = []

    def openqa_group(self):
        return 'openSUSE Leap 15.0'

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':ToTest', self.main_products[0])


class ToTestSLE150(ToTestBaseNew):
    main_products = [
        '000product:SLES-cd-DVD-aarch64',
        '000product:SLES-cd-DVD-ppc64le',
        '000product:SLES-cd-DVD-s390x',
        '000product:SLES-cd-DVD-x86_64',
    ]

    ftp_products = [
        '000product:SLES-ftp-POOL-aarch64',
        '000product:SLES-ftp-POOL-ppc64le',
        '000product:SLES-ftp-POOL-s390x',
        '000product:SLES-ftp-POOL-x86_64',
                    ]

    livecd_products = []

    def __init__(self, *args, **kwargs):
        ToTestBaseNew.__init__(self, test_subproject='TEST', *args, **kwargs)

    def openqa_group(self):
        return 'Functional'

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':TEST', self.main_products[0])

    def ftp_build_version(self, project, tree):
        return super(ToTestSLE150, self).ftp_build_version(project, tree, base='SLE')

    def iso_build_version(self, project, tree):
        return super(ToTestSLE150, self).iso_build_version(project, tree, base='SLE')


class CommandlineInterface(cmdln.Cmdln):

    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

        self.totest_class = {
            'openSUSE:Factory': ToTestFactory,
            'openSUSE:Factory:PowerPC': ToTestFactoryPowerPC,
            'openSUSE:Factory:ARM': ToTestFactoryARM,
            'openSUSE:Factory:zSystems': ToTestFactoryzSystems,
            'openSUSE:Leap:15.0': ToTest150,
            'SUSE:SLE-15:GA': ToTestSLE150,
        }
        self.openqa_server = {
            'openSUSE': 'https://openqa.opensuse.org',
            'SLE': 'https://openqa.suse.de',
        }
        self.api_url = {
            'openSUSE': 'https://api.opensuse.org',
            'SLE': 'https://api.suse.de',
        }

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        parser.add_option(
            "--osc-debug", action="store_true", help="osc debug output")
        parser.add_option(
            "--project-base", help="""Select base of OBS/IBS project as well as openQA server based on distribution family, e.g. 'openSUSE' or 'SLE', default:
            'openSUSE'""")
        parser.add_option(
            "--openqa-server", help="""Full URL to the openQA server that should be queried, default based on '--project-base' selection, e.g.
            'https://openqa.opensuse.org' for 'openSUSE'""")
        parser.add_option(
            "--obs-api-url", help="""Full URL to OBS instance to be queried, default based on '--project-base' selection, e.g.
            'https://api.opensuse.org' for 'openSUSE'""")
        return parser
        return parser

    def postoptparse(self):
        level = None
        if (self.options.debug):
            level = logging.DEBUG
        elif (self.options.verbose):
            level = logging.INFO

        fmt = '%(module)s:%(lineno)d %(levelname)s %(message)s'
        if os.isatty(0):
            fmt = '%(asctime)s - ' + fmt

        logging.basicConfig(level=level, format=fmt)

        osc.conf.get_config()
        if (self.options.osc_debug):
            osc.conf.config['debug'] = True
        if not self.options.project_base:
            self.options.project_base = 'openSUSE'
        if not self.options.openqa_server:
            self.options.openqa_server = self.openqa_server[self.options.project_base]
        if not self.options.obs_api_url:
            self.options.obs_api_url = self.api_url[self.options.project_base]


    def _setup_totest(self, project):
        fallback_project = 'openSUSE:%s' % project
        if project not in self.totest_class and fallback_project in self.totest_class:
            project = fallback_project
        Config(project)
        if project not in self.totest_class:
            msg = 'Project %s not recognized. Possible values [%s]' % (
                project, ', '.join(self.totest_class))
            raise cmdln.CmdlnUserError(msg)

        return self.totest_class[project](project, self.options.dry, self.options.obs_api_url, self.options.openqa_server)

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    def do_run(self, subcmd, opts, project='openSUSE:Factory'):
        """${cmd_name}: run the ToTest Manager

        ${cmd_usage}
        ${cmd_option_list}
        """

        class ExTimeout(Exception):

            """raised on timeout"""

        if opts.interval:
            def alarm_called(nr, frame):
                raise ExTimeout()
            signal.signal(signal.SIGALRM, alarm_called)

        while True:
            try:
                totest = self._setup_totest(project)
                totest.totest()
            except Exception as e:
                logger.error(e)

            if opts.interval:
                if os.isatty(0):
                    logger.info(
                        "sleeping %d minutes. Press enter to check now ..." % opts.interval)
                    signal.alarm(opts.interval * 60)
                    try:
                        raw_input()
                    except ExTimeout:
                        pass
                    signal.alarm(0)
                    logger.info("recheck at %s" %
                                datetime.datetime.now().isoformat())
                else:
                    logger.info("sleeping %d minutes." % opts.interval)
                    time.sleep(opts.interval * 60)
                continue
            break

    def do_release(self, subcmd, opts, project='openSUSE:Factory'):
        """${cmd_name}: manually release all media. Use with caution!

        ${cmd_usage}
        ${cmd_option_list}
        """

        totest = self._setup_totest(project)

        totest.release()


if __name__ == "__main__":
    app = CommandlineInterface()
    sys.exit(app.main())

# vim: sw=4 et
