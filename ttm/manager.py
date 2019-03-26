#! /usr/bin/python
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

import ToolBase
import logging
import json
import re
import pika
import yaml
import osc.conf
from osc.core import makeurl
from osclib.stagingapi import StagingAPI
from xml.etree import cElementTree as ET
try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

from openqa_client.client import OpenQA_Client

from ttm.totest import ToTest

# QA Results
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3

class NotFoundException(Exception):
    pass

class ToTestManager(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.logger = logging.getLogger(__name__)

    def setup(self, project):
        self.project = ToTest(project)
        apiurl = osc.conf.config['apiurl']
        self.api = StagingAPI(apiurl, project=project)
        self.openqa = OpenQA_Client(server=self.project.openqa_server)
        self.update_pinned_descr = False
        self.load_issues_to_ignore()

    def send_amqp_event(self, current_snapshot, current_result):
        amqp_url = osc.conf.config.get('ttm_amqp_url')
        if not amqp_url:
            self.logger.debug('No ttm_amqp_url configured in oscrc - skipping amqp event emission')
            return

        self.logger.debug('Sending AMQP message')
        inf = re.sub(r'ed$', '', self._result2str(current_result))
        msg_topic = '%s.ttm.build.%s' % (self.project.base.lower(), inf)
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
                notify_connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
                notify_channel = notify_connection.channel()
                notify_channel.exchange_declare(exchange='pubsub', exchange_type='topic', passive=True, durable=True)
                notify_channel.basic_publish(exchange='pubsub', routing_key=msg_topic, body=msg_body)
                notify_connection.close()
                break
            except pika.exceptions.ConnectionClosed as e:
                self.logger.warn('Sending AMQP event did not work: %s. Retrying try %s out of %s' % (e, t, tries))
        else:
            self.logger.error('Could not send out AMQP event for %s tries, aborting.' % tries)

    def totest(self, project):
        self.setup(project)
        try:
            current_snapshot = self.get_current_snapshot()
        except NotFoundException as e:
            # nothing in test project (yet)
            self.logger.warn(e)
            current_snapshot = None
        new_snapshot = self.current_sources()
        self.update_pinned_descr = False
        current_result = self.overall_result(current_snapshot)
        current_qa_version = self.current_qa_version()

        self.logger.info('current_snapshot %s: %s' %
                    (current_snapshot, self._result2str(current_result)))
        self.logger.debug('new_snapshot %s', new_snapshot)
        self.logger.debug('current_qa_version %s', current_qa_version)

        snapshotable = self.is_snapshottable()
        self.logger.debug('snapshotable: %s', snapshotable)
        can_release = ((current_snapshot is None or current_result != QA_INPROGRESS) and snapshotable)

        # not overwriting
        if new_snapshot == current_qa_version:
            self.logger.debug('no change in snapshot version')
            can_release = False
        elif not self.all_repos_done(self.project.test_project):
            self.logger.debug("not all repos done, can't release")
            # the repos have to be done, otherwise we better not touch them
            # with a new release
            can_release = False

        self.send_amqp_event(current_snapshot, current_result)

        can_publish = (current_result == QA_PASSED)

        # already published
        totest_is_publishing = self.totest_is_publishing()
        if totest_is_publishing:
            self.logger.debug('totest already publishing')
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
                self.write_version_to_dashboard('snapshot', current_snapshot)
                can_release = False  # we have to wait
            else:
                # We reached a very bad status: openQA testing is 'done', but not of the same version
                # currently in test project. This can happen when 'releasing' the
                # product failed
                raise Exception('Publishing stopped: tested version (%s) does not match version in test project (%s)'
                                % (current_qa_version, current_snapshot))

        if can_release:
            self.update_totest(new_snapshot)
            self.write_version_to_dashboard('totest', new_snapshot)


    def release(self, project):
        self.setup(project)
        new_snapshot = self.current_sources()
        self.update_totest(new_snapshot)

    def write_version_to_dashboard(self, target, version):
        version_file = 'version_%s' % target
        if self.project.is_image_product:
            version_file = version_file + '_images'
        if not (self.dryrun or self.norelease):
            self.api.pseudometa_file_ensure(version_file, version, comment='Update version')

    def load_issues_to_ignore(self):
        text = self.api.attribute_value_load('IgnoredIssues')
        if text:
            root = yaml.safe_load(text)
            self.issues_to_ignore = root.get('last_seen')
        else:
            self.issues_to_ignore = dict()

    def save_issues_to_ignore(self):
        if self.dryrun:
            return
        text = yaml.dump({'last_seen': self.issues_to_ignore}, default_flow_style=False)
        self.api.attribute_value_save('IgnoredIssues', text)

    def release_version(self):
        url = self.api.makeurl(['build', self.project.name, 'standard', self.project.arch,
                                '000release-packages:%s-release' % self.project.base])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)

        raise NotFoundException("can't find %s version" % self.project)

    def current_sources(self):
        if self.project.take_source_from_product is None:
            raise Exception('No idea where to take the source version from')

        if self.project.take_source_from_product:
            if self.project.is_image_product:
                return self.iso_build_version(self.project, self.project.image_products[0].package,
                                              arch=self.project.image_products[0].archs[0])
            return self.iso_build_version(self.project, self.project.main_products[0])
        else:
            return self.release_version()

    def binaries_of_product(self, project, product, repo=None, arch=None):
        if repo is None:
            repo = self.project.product_repo
        if arch is None:
            arch = self.project.product_arch

        url = self.api.makeurl(['build', project, repo, arch, product])
        try:
            f = self.api.retried_GET(url)
        except HTTPError:
            return []

        ret = []
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            ret.append(binary.get('filename'))

        return ret

    def get_current_snapshot(self):
        if self.project.is_image_product:
            return self.iso_build_version(self.project.test_project, self.project.image_products[0].package,
                                          arch=self.project.image_products[0].archs[0])
        return self.iso_build_version(self.project.test_project, self.project.main_products[0])

    def ftp_build_version(self, project, tree):
        for binary in self.binaries_of_product(project, tree):
            result = re.match(r'.*-Build(.*)-Media1.report', binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s ftp version" % project)

    def iso_build_version(self, project, tree, repo=None, arch=None):
        for binary in self.binaries_of_product(project, tree, repo=repo, arch=arch):
            result = re.match(r'.*-(?:Build|Snapshot)([0-9.]+)(?:-Media.*\.iso|\.docker\.tar\.xz|\.raw\.xz)', binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s iso version" % project)

    def current_qa_version(self):
        version_file = 'version_totest'
        if self.project.is_image_product:
            version_file = 'version_totest_images'

        return self.api.pseudometa_file_load(version_file)

    def find_openqa_results(self, snapshot):
        """Return the openqa jobs of a given snapshot and filter out the
        cloned jobs

        """

        url = makeurl(self.project.openqa_server,
                      ['api', 'v1', 'jobs'], {'group': self.project.openqa_group, 'build': snapshot, 'latest': 1})
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

    def update_openqa_status_message(self):
        url = makeurl(self.project.openqa_server,
                      ['api', 'v1', 'job_groups'])
        f = self.api.retried_GET(url)
        job_groups = json.load(f)
        group_id = 0
        for jg in job_groups:
            if jg['name'] == self.project.openqa_group:
                group_id = jg['id']
                break

        if not group_id:
            self.logger.debug('No openQA group id found for status comment update, ignoring')
            return

        pinned_ignored_issue = 0
        issues = ' , '.join(self.issues_to_ignore.keys())
        status_flag = 'publishing' if self.status_for_openqa['is_publishing'] else \
            'preparing' if self.status_for_openqa['can_release'] else \
            'testing' if self.status_for_openqa['snapshotable'] else \
            'building'
        status_msg = 'tag:{}:{}:{}'.format(self.status_for_openqa['new_snapshot'], status_flag, status_flag)
        msg = 'pinned-description: Ignored issues\r\n\r\n{}\r\n\r\n{}'.format(issues, status_msg)
        data = {'text': msg}

        url = makeurl(self.project.openqa_server,
                      ['api', 'v1', 'groups', str(group_id), 'comments'])
        f = self.api.retried_GET(url)
        comments = json.load(f)
        for comment in comments:
            if comment['userName'] == 'ttm' and \
                    comment['text'].startswith('pinned-description: Ignored issues'):
                pinned_ignored_issue = comment['id']

        self.logger.debug('Writing openQA status message: {}'.format(data))
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

        if len(jobs) < self.project.jobs_num:  # not yet scheduled
            self.logger.warning('we have only %s jobs' % len(jobs))
            return QA_INPROGRESS

        in_progress = False
        for job in jobs:
            # print json.dumps(job, sort_keys=True, indent=4)
            if job['result'] in ('failed', 'incomplete', 'skipped', 'user_cancelled', 'obsoleted', 'parallel_failed'):
                # print json.dumps(job, sort_keys=True, indent=4), jobname
                url = makeurl(self.project.openqa_server,
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
                            self.logger.info('Would label {} with: {}'.format(job['id'], text))
                        else:
                            self.openqa.openqa_request(
                                'PUT', 'jobs/%s/comments/%d' % (job['id'], labeled), data=data)

                    self.logger.info('job %s failed, but was ignored', job['name'])
                else:
                    self.failed_relevant_jobs.append(job['id'])
                    if not labeled and len(refs) > 0:
                        data = {'text': 'label:unknown_failure'}
                        if self.dryrun:
                            self.logger.info('Would label {} as unknown'.format(job['id']))
                        else:
                            self.openqa.openqa_request(
                                'POST', 'jobs/%s/comments' % job['id'], data=data)

                    joburl = '%s/tests/%s' % (self.project.openqa_server, job['id'])
                    self.logger.info('job %s failed, see %s', job['name'], joburl)

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
                self.logger.info('%s %s %s -> %s' % (repo.get('project'),
                                                repo.get('repository'), repo.get('arch'), 'dirty'))
                ready = False
            if repo.get('code') not in codes:
                self.logger.info('%s %s %s -> %s' % (repo.get('project'),
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

        # containers have no size limit
        if re.match(r'(opensuse|kubic)-.*-image.*', package):
            return None

        if '-Addon-NonOss-ftp-ftp' in package:
            return None

        if 'JeOS' in package or 'Kubic' in package:
            return 4700372992

        raise Exception('No maxsize for {}'.format(package))

    def package_ok(self, project, package, repository, arch):
        """Checks one package in a project and returns True if it's succeeded

        """

        query = {'package': package, 'repository': repository, 'arch': arch}

        url = self.api.makeurl(['build', project, '_result'], query)
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        # [@code!='succeeded'] is not supported by ET
        failed = [status for status in root.findall('result/status') if status.get('code') != 'succeeded']

        if any(failed):
            self.logger.info(
                '%s %s %s %s -> %s' % (project, package, repository, arch, failed[0].get('code')))
            return False

        if not len(root.findall('result/status[@code="succeeded"]')):
            self.logger.info('No "succeeded" for %s %s %s %s' % (project, package, repository, arch))
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
                self.logger.error('%s %s %s %s: %s' % (
                    project, package, repository, arch, 'too large by %s bytes' % (isosize - maxsize)))
                return False

        return True

    def is_snapshottable(self):
        """Check various conditions required for factory to be snapshotable

        """

        if not self.all_repos_done(self.project.name):
            return False

        for product in self.project.ftp_products + self.project.main_products:
            if not self.package_ok(self.project.name, product, self.project.product_repo, self.project.product_arch):
                return False

        for product in self.project.image_products + self.project.container_products:
            for arch in product.archs:
                if not self.package_ok(self.project.name, product.package, self.project.product_repo, arch):
                    return False

        if len(self.project.livecd_products):
            if not self.all_repos_done('%s:Live' % self.project.name):
                return False

            for product in self.project.livecd_products:
                for arch in product.archs:
                    if not self.package_ok('%s:Live' % self.project.name, product.package,
                                           self.project.product_repo, arch):
                        return False

        if self.project.need_same_build_number:
            # make sure all medias have the same build number
            builds = set()
            for p in self.project.ftp_products:
                if 'Addon-NonOss' in p:
                    # XXX: don't care about nonoss atm.
                    continue
                builds.add(self.ftp_build_version(self.project.name, p))
            for p in self.project.main_products:
                builds.add(self.iso_build_version(self.project.name, p))
            for p in self.project.livecd_products + self.project.image_products:
                for arch in p.archs:
                    builds.add(self.iso_build_version(self.project.name, p.package,
                                                      arch=arch))
            if len(builds) != 1:
                self.logger.debug('not all medias have the same build number')
                return False

        return True

    def _release_package(self, project, package, set_release=None, repository=None,
                         target_project=None, target_repository=None):
        if package.startswith('000product:'):
            self.logger.debug('Ignoring to release {}'.format(package))
            return

        query = {'cmd': 'release'}

        if set_release:
            query['setrelease'] = set_release

        if repository is not None:
            query['repository'] = repository

        if target_project is not None:
            # Both need to be set
            query['target_project'] = target_project
            query['target_repository'] = target_repository

        baseurl = ['source', project, package]

        url = self.api.makeurl(baseurl, query=query)
        if self.dryrun or self.norelease:
            self.logger.info('release %s/%s (%s)' % (project, package, query))
        else:
            self.api.retried_POST(url)

    def _release(self, set_release=None):
        # release 000product as a whole
        if self.project.main_products[0].startswith('000product'):
            self._release_package(self.project, '000product', set_release=set_release)

        for product in self.project.ftp_products:
            self._release_package(self.project, product, repository=self.project.product_repo)

        for cd in self.project.livecd_products:
            self._release_package('%s:Live' %
                                  self.project, cd.package, set_release=set_release,
                                  repository=self.livecd_repo)

        for image in self.project.image_products:
            self._release_package(self.project, image.package, set_release=set_release,
                                  repository=self.project.product_repo)

        for cd in self.project.main_products:
            self._release_package(self.project, cd, set_release=set_release,
                                  repository=self.project.product_repo)

        for container in self.project.container_products:
            # Containers are built in the same repo as other image products,
            # but released into a different repo in :ToTest
            self._release_package(self.project, container.package, repository=self.project.product_repo,
                                  target_project=self.project.test_project,
                                  target_repository=self.project.totest_container_repo)

    def update_totest(self, snapshot=None):
        # omit snapshot, we don't want to rename on release
        if not self.project.set_snapshot_number:
            snapshot = None
        release = 'Snapshot%s' % snapshot if snapshot else None
        self.logger.info('Updating snapshot %s' % snapshot)
        if not (self.dryrun or self.norelease):
            self.api.switch_flag_in_prj(self.project.test_project, flag='publish', state='disable',
                                        repository=self.project.product_repo)

        self._release(set_release=release)

    def publish_factory_totest(self):
        self.logger.info('Publish test project content')
        if not (self.dryrun or self.norelease):
            self.api.switch_flag_in_prj(
                self.project.test_project, flag='publish', state='enable',
                repository=self.project.product_repo)
        if self.project.container_products:
            self.logger.info('Releasing container products from ToTest')
            for container in self.project.container_products:
                self._release_package(self.project.test_project, container.package,
                                      repository=self.project.totest_container_repo)

    def totest_is_publishing(self):
        """Find out if the publishing flag is set in totest's _meta"""

        url = self.api.makeurl(
            ['source', self.project.test_project, '_meta'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        if not root.find('publish'):  # default true
            return True

        for flag in root.find('publish'):
            if flag.get('repository', None) not in [None, self.project.product_repo]:
                continue
            if flag.get('arch', None):
                continue
            if flag.tag == 'enable':
                return True
        return False
