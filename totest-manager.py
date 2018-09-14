#!/usr/bin/python2
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

from __future__ import print_function

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
import yaml
import pika

from xml.etree import cElementTree as ET
from openqa_client.client import OpenQA_Client

import osc

from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osc.core import makeurl

logger = logging.getLogger()

# QA Results
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3


class NotFoundException(Exception):
    pass


class ToTestBase(object):

    """Base class to store the basic interface"""

    product_repo = 'images'
    product_arch = 'local'
    livecd_repo = 'images'
    livecd_archs = ['i586', 'x86_64']

    def __init__(self, project, dryrun=False, norelease=False, api_url=None, openqa_server='https://openqa.opensuse.org', test_subproject=None):
        self.project = project
        self.dryrun = dryrun
        self.norelease = norelease
        if not api_url:
            api_url = osc.conf.config['apiurl']
        self.api = StagingAPI(api_url, project=project)
        self.openqa_server = openqa_server
        if not test_subproject:
            test_subproject = 'ToTest'
        self.test_project = '%s:%s' % (self.project, test_subproject)
        self.openqa = OpenQA_Client(server=openqa_server)
        self.load_issues_to_ignore()
        self.project_base = project.split(':')[0]
        self.update_pinned_descr = False
        self.amqp_url = osc.conf.config.get('ttm_amqp_url')

    def load_issues_to_ignore(self):
        text = self.api.attribute_value_load('IgnoredIssues')
        if text:
            root = yaml.load(text)
            self.issues_to_ignore = root.get('last_seen')
        else:
            self.issues_to_ignore = dict()

    def save_issues_to_ignore(self):
        if self.dryrun:
            return
        text = yaml.dump({'last_seen': self.issues_to_ignore}, default_flow_style=False)
        self.api.attribute_value_save('IgnoredIssues', text)

    def openqa_group(self):
        return self.project

    def iso_prefix(self):
        return self.project

    def jobs_num(self):
        return 70

    def current_version(self):
        return self.release_version()

    def binaries_of_product(self, project, product):
        url = self.api.makeurl(['build', project, self.product_repo, self.product_arch, product])
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

        for binary in self.binaries_of_product(self.test_project, '000product:%s-cd-mini-%s' % (self.project_base, self.arch())):
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
            result = re.match(r'.*-(?:Build|Snapshot)([0-9.]+)(?:-Media.*\.iso|\.docker\.tar\.xz)', binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s iso version" % project)

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

        raise NotFoundException("can't find %s version" % self.project)

    def current_qa_version(self):
        return self.api.pseudometa_file_load('version_totest')

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
        issues = ' , '.join(self.issues_to_ignore.keys())
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

        self.failed_relevant_jobs = []
        self.failed_ignored_jobs = []

        if len(jobs) < self.jobs_num():  # not yet scheduled
            logger.warning('we have only %s jobs' % len(jobs))
            return QA_INPROGRESS

        in_progress = False
        for job in jobs:
            # print json.dumps(job, sort_keys=True, indent=4)
            if job['result'] in ('failed', 'incomplete', 'skipped', 'user_cancelled', 'obsoleted', 'parallel_failed'):
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
                    if re.search(r'@ttm:? ignore', comment['text']):
                        to_ignore = True
                # to_ignore can happen with or without refs
                ignored = True if to_ignore else len(refs) > 0
                build_nr = str(job['settings']['BUILD'])
                for ref in refs:
                    if ref not in self.issues_to_ignore:
                        if to_ignore:
                            self.issues_to_ignore[ref] = build_nr
                            self.update_pinned_descr = True
                        else:
                            ignored = False
                    else:
                        # update reference
                        self.issues_to_ignore[ref] = build_nr

                if ignored:
                    self.failed_ignored_jobs.append(job['id'])
                    if labeled:
                        text = 'Ignored issue' if len(refs) > 0 else 'Ignored failure'
                        # remove flag - unfortunately can't delete comment unless admin
                        data = {'text': text}
                        if self.dryrun:
                            logger.info("Would label {} with: {}".format(job['id'], text))
                        else:
                            self.openqa.openqa_request(
                                'PUT', 'jobs/%s/comments/%d' % (job['id'], labeled), data=data)

                    logger.info("job %s failed, but was ignored", job['name'])
                else:
                    self.failed_relevant_jobs.append(job['id'])
                    if not labeled and len(refs) > 0:
                        data = {'text': 'label:unknown_failure'}
                        if self.dryrun:
                            logger.info("Would label {} as unknown".format(job['id']))
                        else:
                            self.openqa.openqa_request(
                                'POST', 'jobs/%s/comments' % job['id'], data=data)

                    joburl = '%s/tests/%s' % (self.openqa_server, job['id'])
                    logger.info("job %s failed, see %s", job['name'], joburl)

            elif job['result'] == 'passed' or job['result'] == 'softfailed':
                continue
            elif job['result'] == 'none':
                if job['state'] != 'cancelled':
                    in_progress = True
            else:
                raise Exception(job['result'])

        self.save_issues_to_ignore()

        if len(self.failed_relevant_jobs) > 0:
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

        # docker container has no size limit
        if re.match(r'opensuse-leap-image.*', package):
            return None

        if '-Addon-NonOss-ftp-ftp' in package:
            return None

        if 'JeOS' in package:
            return 4700372992

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
            if not self.package_ok(self.project, product, self.product_repo, self.product_arch):
                return False

            if len(self.livecd_products):

                if not self.all_repos_done('%s:Live' % self.project):
                    return False

                for arch in self.livecd_archs:
                    for product in self.livecd_products:
                        if not self.package_ok('%s:Live' % self.project, product, self.livecd_repo, arch):
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
        if self.dryrun or self.norelease:
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
        if not (self.dryrun or self.norelease):
            self.api.switch_flag_in_prj(self.test_project, flag='publish', state='disable')

        self._release(set_release=release)

    def publish_factory_totest(self):
        logger.info('Publish test project content')
        if not (self.dryrun or self.norelease):
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
        current_qa_version = self.current_qa_version()

        logger.info('current_snapshot %s: %s' %
                    (current_snapshot, self._result2str(current_result)))
        logger.debug('new_snapshot %s', new_snapshot)
        logger.debug('current_qa_version %s', current_qa_version)

        snapshotable = self.is_snapshottable()
        logger.debug("snapshotable: %s", snapshotable)
        can_release = ((current_snapshot is None or current_result != QA_INPROGRESS) and snapshotable)

        # not overwriting
        if new_snapshot == current_snapshot:
            logger.debug("no change in snapshot version")
            can_release = False
        elif not self.all_repos_done(self.test_project):
            logger.debug("not all repos done, can't release")
            # the repos have to be done, otherwise we better not touch them
            # with a new release
            can_release = False

        self.send_amqp_event(current_snapshot, current_result)

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

    def send_amqp_event(self, current_snapshot, current_result):
        if not self.amqp_url:
            logger.debug('No ttm_amqp_url configured in oscrc - skipping amqp event emission')
            return

        logger.debug('Sending AMQP message')
        inf = re.sub(r"ed$", '', self._result2str(current_result))
        msg_topic = '%s.ttm.build.%s' % (self.project_base.lower(), inf)
        msg_body = json.dumps({
            'build': current_snapshot,
            'project': self.project,
            'failed_jobs': {
                'relevant': self.failed_relevant_jobs,
                'ignored': self.failed_ignored_jobs,
            }
        })

        # send amqp event
        tries = 7  # arbitrary
        for t in range(tries):
            try:
                notify_connection = pika.BlockingConnection(pika.URLParameters(self.amqp_url))
                notify_channel = notify_connection.channel()
                notify_channel.exchange_declare(exchange='pubsub', exchange_type='topic', passive=True, durable=True)
                notify_channel.basic_publish(exchange='pubsub', routing_key=msg_topic, body=msg_body)
                notify_connection.close()
                break
            except pika.exceptions.ConnectionClosed as e:
                logger.warn('Sending AMQP event did not work: %s. Retrying try %s out of %s' % (e, t, tries))
        else:
            logger.error('Could not send out AMQP event for %s tries, aborting.' % tries)

    def release(self):
        new_snapshot = self.current_version()
        self.update_totest(new_snapshot)

    def write_version_to_dashboard(self, target, version):
        if not (self.dryrun or self.norelease):
            self.api.pseudometa_file_ensure('version_%s' % target, version, comment='Update version')


class ToTestBaseNew(ToTestBase):

    # whether all medias need to have the same build number
    need_same_build_number = True

    # whether to set a snapshot number on release
    set_snapshot_number = False

    """Base class for new product builder"""

    def _release(self, set_release=None):
        query = {'cmd': 'release'}

        package = '000product'
        project = self.project

        if set_release:
            query['setrelease'] = set_release

        baseurl = ['source', project, package]

        url = self.api.makeurl(baseurl, query=query)
        if self.dryrun or self.norelease:
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
        if ret and self.need_same_build_number:
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
            if ret is False:
                logger.debug("not all medias have the same build number")

        return ret

    def update_totest(self, snapshot):
        if not self.set_snapshot_number:
            snapshot = None
        # omit snapshot, we don't want to rename on release
        super(ToTestBaseNew, self).update_totest(snapshot)


class ToTestFactory(ToTestBase):
    main_products = ['000product:openSUSE-dvd5-dvd-i586',
                     '000product:openSUSE-dvd5-dvd-x86_64',
                     '000product:openSUSE-cd-mini-i586',
                     '000product:openSUSE-cd-mini-x86_64',
                     '000product:openSUSE-Tumbleweed-Kubic-dvd5-dvd-x86_64']

    ftp_products = ['000product:openSUSE-ftp-ftp-i586_x86_64',
                    '000product:openSUSE-Addon-NonOss-ftp-ftp-i586_x86_64']

    livecd_products = ['livecd-tumbleweed-kde',
                       'livecd-tumbleweed-gnome',
                       'livecd-tumbleweed-x11']

    def __init__(self, *args, **kwargs):
        ToTestBase.__init__(self, *args, **kwargs)

    def openqa_group(self):
        return 'openSUSE Tumbleweed'

    def iso_prefix(self):
        return 'Tumbleweed'

    def arch(self):
        return 'x86_64'


class ToTestFactoryPowerPC(ToTestBase):
    main_products = ['000product:openSUSE-dvd5-dvd-ppc64',
                     '000product:openSUSE-dvd5-dvd-ppc64le',
                     '000product:openSUSE-cd-mini-ppc64',
                     '000product:openSUSE-cd-mini-ppc64le']

    ftp_products = ['000product:openSUSE-ftp-ftp-ppc64_ppc64le']

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
    main_products = ['000product:openSUSE-cd-mini-aarch64',
                     '000product:openSUSE-dvd5-dvd-aarch64']

    ftp_products = ['000product:openSUSE-ftp-ftp-aarch64',
                    '000product:openSUSE-ftp-ftp-armv7hl',
                    '000product:openSUSE-ftp-ftp-armv6hl']

    livecd_products = ['JeOS']
    livecd_archs = ['armv7l']

    def __init__(self, *args, **kwargs):
        ToTestFactory.__init__(self, *args, **kwargs)

    def openqa_group(self):
        return 'openSUSE Tumbleweed AArch64'

    def arch(self):
        return 'aarch64'

    def jobs_num(self):
        return 2


class ToTest151(ToTestBaseNew):
    main_products = [
        '000product:openSUSE-cd-mini-x86_64',
        '000product:openSUSE-dvd5-dvd-x86_64',
    ]

    ftp_products = ['000product:openSUSE-ftp-ftp-x86_64',
                    '000product:openSUSE-Addon-NonOss-ftp-ftp-x86_64'
                    ]

    livecd_products = []

    def openqa_group(self):
        return 'openSUSE Leap 15'

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':ToTest', self.main_products[0])


class ToTest151ARM(ToTestBaseNew):
    main_products = [
        '000product:openSUSE-cd-mini-aarch64',
        '000product:openSUSE-dvd5-dvd-aarch64',
    ]

    ftp_products = ['000product:openSUSE-ftp-ftp-aarch64',
                    '000product:openSUSE-ftp-ftp-armv7hl',
                    ]

    livecd_products = ['JeOS']
    livecd_archs = ['armv7l']

    # Leap 15.1 ARM still need to update snapshot
    set_snapshot_number = True

    # product_repo openqa_group jobs_num values are specific to aarch64
    # TODO: How to handle the other entries of main_products ?

    def openqa_group(self):
        return 'openSUSE Leap 15 AArch64'

    def jobs_num(self):
        return 10

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':ToTest', self.main_products[0])


class ToTest150Ports(ToTestBaseNew):
    main_products = [
        '000product:openSUSE-cd-mini-aarch64',
        '000product:openSUSE-dvd5-dvd-aarch64',
    ]

    ftp_products = ['000product:openSUSE-ftp-ftp-aarch64',
                    '000product:openSUSE-ftp-ftp-armv7hl',
                    ]

    livecd_products = []

    # Leap 15.0 Ports still need to update snapshot
    set_snapshot_number = True

    # product_repo openqa_group jobs_num values are specific to aarch64
    # TODO: How to handle the other entries of main_products ?

    product_repo = 'images_arm'

    def openqa_group(self):
        return 'openSUSE Leap 15.0 AArch64'

    def jobs_num(self):
        return 10

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':ToTest', self.main_products[0])


class ToTest150Images(ToTestBaseNew):
    main_products = [
        'livecd-leap-gnome',
        'livecd-leap-kde',
        'livecd-leap-x11',
        'opensuse-leap-image:docker',
        'opensuse-leap-image:lxc',
        'kiwi-templates-Leap15-JeOS:MS-HyperV',
        'kiwi-templates-Leap15-JeOS:OpenStack-Cloud',
        'kiwi-templates-Leap15-JeOS:VMware',
        'kiwi-templates-Leap15-JeOS:XEN',
        'kiwi-templates-Leap15-JeOS:kvm-and-xen',
    ]

    ftp_products = []

    livecd_products = []
    product_arch = 'x86_64'

    # docker image has a different number
    need_same_build_number = False
    set_snapshot_number = True

    def openqa_group(self):
        return 'openSUSE Leap 15.0 Images'

    def current_qa_version(self):
        return self.api.pseudometa_file_load('version_totest_images')

    def write_version_to_dashboard(self, target, version):
        super(ToTest150Images, self).write_version_to_dashboard('{}_images'.format(target), version)

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':ToTest', self.main_products[0])

    def _release(self, set_release=None):
        ToTestBase._release(self, set_release)

    def jobs_num(self):
        return 13

class ToTest151Images(ToTest150Images):

    def openqa_group(self):
        return 'openSUSE Leap 15.1 Images'


class ToTestSLE(ToTestBaseNew):
    def __init__(self, *args, **kwargs):
        ToTestBaseNew.__init__(self, test_subproject='TEST', *args, **kwargs)

    def openqa_group(self):
        return 'Functional'

    def get_current_snapshot(self):
        return self.iso_build_version(self.project + ':TEST', self.main_products[0])

    def ftp_build_version(self, project, tree):
        return super(ToTestSLE, self).ftp_build_version(project, tree, base='SLE')

    def iso_build_version(self, project, tree):
        return super(ToTestSLE, self).iso_build_version(project, tree, base='SLE')

class ToTestSLE12(ToTestSLE):
    main_products = [
        '_product:SLES-dvd5-DVD-aarch64',
        '_product:SLES-dvd5-DVD-ppc64le',
        '_product:SLES-dvd5-DVD-s390x',
        '_product:SLES-dvd5-DVD-x86_64',
    ]

    ftp_products = [
        '_product:SLES-ftp-POOL-aarch64',
        '_product:SLES-ftp-POOL-ppc64le',
        '_product:SLES-ftp-POOL-s390x',
        '_product:SLES-ftp-POOL-x86_64',
    ]

    livecd_products = []

class ToTestSLE15(ToTestSLE):
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


class CommandlineInterface(cmdln.Cmdln):

    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)

        self.totest_class = {
            'openSUSE:Factory': ToTestFactory,
            'openSUSE:Factory:PowerPC': ToTestFactoryPowerPC,
            'openSUSE:Factory:ARM': ToTestFactoryARM,
            'openSUSE:Factory:zSystems': ToTestFactoryzSystems,
            'openSUSE:Leap:15.1': ToTest151,
            'openSUSE:Leap:15.1:ARM': ToTest151ARM,
            'openSUSE:Leap:15.0:Ports': ToTest150Ports,
            'openSUSE:Leap:15.0:Images': ToTest150Images,
            'openSUSE:Leap:15.1:Images': ToTest151Images,
            'SUSE:SLE-12-SP4:GA': ToTestSLE12,
            'SUSE:SLE-15:GA': ToTestSLE15,
            'SUSE:SLE-15-SP1:GA': ToTestSLE15,
        }
        self.openqa_server = {
            'openSUSE': 'https://openqa.opensuse.org',
            'SUSE': 'https://openqa.suse.de',
        }
        self.api_url = {
            'openSUSE': 'https://api.opensuse.org',
            'SUSE': 'https://api.suse.de',
        }

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--release", action="store_true", help="trigger release in build service (default for openSUSE)")
        parser.add_option("--norelease", action="store_true", help="do not trigger release in build service (default for SLE)")
        parser.add_option("--verbose", action="store_true", help="verbose")
        parser.add_option(
            "--osc-debug", action="store_true", help="osc debug output")
        parser.add_option(
            "--openqa-server", help="""Full URL to the openQA server that should be queried, default based on project selection, e.g.
            'https://openqa.opensuse.org' for 'openSUSE'""")
        parser.add_option(
            "--obs-api-url", help="""Full URL to OBS instance to be queried, default based on project selection, e.g.
            'https://api.opensuse.org' for 'openSUSE'""")
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

    def _setup_totest(self, project):
        fallback_project = 'openSUSE:%s' % project
        if project not in self.totest_class and fallback_project in self.totest_class:
            project = fallback_project

        project_base = project.split(':')[0]
        if not self.options.openqa_server:
            self.options.openqa_server = self.openqa_server[project_base]
        if not self.options.obs_api_url:
            self.options.obs_api_url = self.api_url[project_base]

        Config(self.options.obs_api_url, project)
        if project not in self.totest_class:
            msg = 'Project %s not recognized. Possible values [%s]' % (
                project, ', '.join(self.totest_class))
            raise cmdln.CmdlnUserError(msg)

        if self.options.release:
            release = True
        elif self.options.norelease:
            release = False
        else:
            release = (project_base == 'openSUSE')

        return self.totest_class[project](project, self.options.dry, not release, self.options.obs_api_url, self.options.openqa_server)

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
