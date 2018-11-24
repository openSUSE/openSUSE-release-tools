#!/usr/bin/python

from __future__ import print_function

import argparse
import json
import logging
import os

import osc
from osc.core import http_GET, http_POST, makeurl

import gitlab
from lxml import etree as ET
try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

from PubSubConsumer import PubSubConsumer


class Listener(PubSubConsumer):
    def __init__(self, apiurl, amqp_prefix, gitlab):
        super(Listener, self).__init__(amqp_prefix, logging.getLogger(__name__))
        self.apiurl = apiurl
        self.gitlab = gitlab
        self.glproject = gitlab.projects.get('coolo/citest')
        self.gltrigger = self.get_trigger()
        self.projects = dict()
        self.amqp_prefix = amqp_prefix
        self.timer_id = None
        self.lastcommit = None

    def add(self, project):
        self.projects[project] = {}

    def routing_keys(self):
        ret = []
        for suffix in ['.obs.repo.build_started', '.obs.package.create', '.obs.package.commit', '.obs.package.delete']:
            ret.append(self.amqp_prefix + suffix)
        return ret

    def restart_timer(self):
        if self.timer_id:
            self._connection.remove_timeout(self.timer_id)
            self.timer_id = self._connection.add_timeout(700, self.recheck_projects)
        else:
            self.timer_id = self._connection.add_timeout(0, self.initial_state)

    def get_trigger(self):
        for t in self.glproject.triggers.list():
            if t.description == 'SourcesChanged':
                return t

    def trigger_pipeline(self, project):
        pipeline = self.glproject.trigger_pipeline('master', self.gltrigger.token, variables={"STAGING_API": self.apiurl, "STAGING_PROJECT": project})
        self.projects[project]['pipeline'] = pipeline
        print("triggered pipeline for {}: {}".format(project, pipeline.id))

    def current_commit(self):
        return self.glproject.commits.get('master').short_id

    def recheck_projects(self, reset=False):
        newcommit = self.current_commit()
        if newcommit != self.lastcommit:
            for pipeline in self.glproject.pipelines.list(scope='running'):
                print("canceled pipeline {}".format(pipeline.id))
                pipeline.cancel()
            self.lastcommit = newcommit
            for project in self.projects:
                self.trigger_pipeline(project)
        self.restart_timer()

    def initial_state(self):
        self.recheck_projects(True)

    def start_consuming(self):
        self.restart_timer()
        super(Listener, self).start_consuming()

    def build_job(self, project, repository):
        pipeline = self.projects[project]['pipeline']
        # only the last in list is *the one*
        build = None
        for job in pipeline.jobs.list():
            if job.name == 'build_' + repository:
                build = job
        return build

    def on_message(self, unused_channel, method, properties, body):
        try:
            body = json.loads(body)
            if not body.get('project') in self.projects:
                return
        except ValueError:
            return
        project = body['project']
        if method.routing_key.find('repo.build_started') > 0:
            job = self.build_job(project, body['repo'])
            if job and job.status not in ['pending', 'running', 'created']:
                print(method.routing_key, body, job.status, "restart pipeline")
                # restart build pipeline
                self.projects[project]['pipeline'].cancel()
                self.trigger_pipeline(project)
        if method.routing_key.find('.package.') > 0:
            if body['package'] != '000package-groups':
                return
            self.projects[project]['pipeline'].cancel()
            self.trigger_pipeline(project)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Watch if gitlab pipeline has to be restarted')
    parser.add_argument("--apiurl", '-A', type=str, help='API URL of OBS')
    parser.add_argument('-p', '--project', type=str, help='Project to check')
    parser.add_argument('--token', type=str, help='Gitlab private token')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']

    glapi = None
    if apiurl.endswith('suse.de'):
        amqp_prefix = 'suse'
        glapi = gitlab.Gitlab('https://gitlab.suse.de', private_token=args.token)
    else:
        amqp_prefix = 'opensuse'

    logging.basicConfig(level=logging.INFO)

    listener = Listener(apiurl, amqp_prefix, glapi)
    if args.project:
        listener.add(args.project)
    else:
        url = makeurl(apiurl, ['search', 'project', 'id'], {'match': 'attribute/@name="OSRT:GitlabPipeline"'})
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for entry in root.findall('project'):
            listener.add(entry.get('name'))

    try:
        listener.run()
    except KeyboardInterrupt:
        listener.stop()
