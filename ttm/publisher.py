# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# (C) 2019 coolo@suse.de, SUSE
# Distribute under GPLv2 or GPLv3


import json
import re
import yaml
import pika
import time

import osc
from osc.core import makeurl
from ttm.manager import ToTestManager, NotFoundException, QAResult
from openqa_client.client import OpenQA_Client

class ToTestPublisher(ToTestManager):

    def __init__(self, tool):
        ToTestManager.__init__(self, tool)

    def setup(self, project):
        super(ToTestPublisher, self).setup(project)
        self.openqa = OpenQA_Client(server=self.project.openqa_server)
        self.update_pinned_descr = False
        self.load_issues_to_ignore()

    def overall_result(self, snapshot):
        """Analyze the openQA jobs of a given snapshot Returns a QAResult"""

        if snapshot is None:
            return QAResult.failed

        jobs = self.find_openqa_results(snapshot)

        self.failed_relevant_jobs = []
        self.failed_ignored_jobs = []

        if len(jobs) < self.project.jobs_num:  # not yet scheduled
            self.logger.warning('we have only %s jobs' % len(jobs))
            return QAResult.inprogress

        in_progress = False
        for job in jobs:
            # print json.dumps(job, sort_keys=True, indent=4)
            if job['result'] in ('failed', 'incomplete', 'timeout_exceeded', 'skipped', 'user_cancelled', 'obsoleted', 'parallel_failed'):
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
            return QAResult.failed

        if in_progress:
            return QAResult.inprogress

        return QAResult.passed

    def send_amqp_event(self, current_snapshot, current_result):
        amqp_url = osc.conf.config.get('ttm_amqp_url')
        if not amqp_url:
            self.logger.debug('No ttm_amqp_url configured in oscrc - skipping amqp event emission')
            return

        self.logger.debug('Sending AMQP message')
        inf = re.sub(r'ed$', '', str(current_result))
        msg_topic = '%s.ttm.build.%s' % (self.project.base.lower(), inf)
        msg_body = json.dumps({
            'build': current_snapshot,
            'project': self.project.name,
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
                self.logger.warning('Sending AMQP event did not work: %s. Retrying try %s out of %s' % (e, t, tries))
        else:
            self.logger.error('Could not send out AMQP event for %s tries, aborting.' % tries)

    def publish(self, project, force=False):
        self.setup(project)

        if not self.get_status('testing'):
            # migrating to the attribute status

            try:
                self.update_status('testing', self.version_from_totest_project())
            except NotFoundException:
                self.logger.error('Nothing in totest - release something first')
                return None

            self.update_status('publishing', self.api.pseudometa_file_load(self.version_file('snapshot')))

        current_snapshot = self.get_status('testing')

        group_id = self.openqa_group_id()

        if self.get_status('publishing') == current_snapshot:
            self.logger.info('{} is already publishing'.format(current_snapshot))
            # migrating - if there is no published entry, the last publish call
            # didn't wait for publish - and as such didn't set published state
            if self.get_status('published') != current_snapshot:
                return QAResult.passed
            return None

        current_result = self.overall_result(current_snapshot)
        current_qa_version = self.current_qa_version()

        self.logger.info('current_snapshot {}: {}'.format(current_snapshot, str(current_result)))
        self.logger.debug('current_qa_version {}'.format(current_qa_version))

        self.send_amqp_event(current_snapshot, current_result)

        if current_result == QAResult.failed:
            self.update_status('failed', current_snapshot)
            return QAResult.failed
        else:
            self.update_status('failed', '')

        if current_result != QAResult.passed:
            return QAResult.inprogress

        if current_qa_version != current_snapshot:
            # We reached a very bad status: openQA testing is 'done', but not of the same version
            # currently in test project. This can happen when 'releasing' the
            # product failed
            raise Exception('Publishing stopped: tested version (%s) does not match version in test project (%s)'
                            % (current_qa_version, current_snapshot))

        self.publish_factory_totest()
        self.write_version_to_dashboard('snapshot', current_snapshot)
        self.update_status('publishing', current_snapshot)
        return QAResult.passed

    def wait_for_published(self, project, force=False):
        self.setup(project)

        if not force:
            wait_time = 20
            while not self.all_repos_done(self.project.test_project):
                if self.dryrun:
                    self.logger.info('{} is still not published, do not wait as dryrun.'.format(self.project.test_project))
                    return
                self.logger.info('{} is still not published, waiting {} seconds'.format(self.project.test_project, wait_time))
                time.sleep(wait_time)

        current_snapshot = self.get_status('publishing')
        if self.dryrun:
            self.logger.info('Publisher finished, updating published snapshot to {}'.format(current_snapshot))
            return

        self.update_status('published', current_snapshot)
        group_id = self.openqa_group_id()
        if not group_id:
            return

        self.add_published_tag(group_id, current_snapshot)
        if self.update_pinned_descr:
            self.update_openqa_status_message(group_id)

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

    def add_published_tag(self, group_id, snapshot):
        if self.dryrun:
            return

        url = makeurl(self.project.openqa_server,
                      ['api', 'v1', 'groups', str(group_id), 'comments'])

        status_flag = 'published'
        data = {'text': 'tag:{}:{}:{}'.format(snapshot, status_flag, status_flag) }
        self.openqa.openqa_request('POST', 'groups/%s/comments' % group_id, data=data)

    def openqa_group_id(self):
        url = makeurl(self.project.openqa_server,
                      ['api', 'v1', 'job_groups'])
        f = self.api.retried_GET(url)
        job_groups = json.load(f)
        for jg in job_groups:
            if jg['name'] == self.project.openqa_group:
                return jg['id']

        self.logger.debug('No openQA group id found for status comment update, ignoring')

    def update_openqa_status_message(self, group_id):
        pinned_ignored_issue = 0
        issues = ' , '.join(self.issues_to_ignore.keys())
        msg = 'pinned-description: Ignored issues\r\n\r\n{}'.format(issues)
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

    def publish_factory_totest(self):
        self.logger.info('Publish test project content')
        if self.dryrun or self.project.do_not_release:
            return
        if self.project.container_products:
            self.logger.info('Releasing container products from ToTest')
            for container in self.project.container_products:
                self.release_package(self.project.test_project, container.package,
                                      repository=self.project.totest_container_repo)
        self.api.switch_flag_in_prj(
            self.project.test_project, flag='publish', state='enable',
            repository=self.project.product_repo)

        if self.project.totest_images_repo != self.project.product_repo:
            self.logger.info('Publish test project content (image_products)')
            self.api.switch_flag_in_prj(self.project.test_project, flag='publish', state='enable',
            repository=self.project.totest_images_repo)
