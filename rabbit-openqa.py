#!/usr/bin/python

import argparse
import pika
import sys
import json
import osc
import re
from osc.core import http_POST
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from lxml import etree as ET
from openqa_client.client import OpenQA_Client
from urllib import quote_plus
import requests
try:
    from urllib.error import HTTPError
except ImportError:
    #python 2.x
    from urllib2 import HTTPError

class Project(object):
    def __init__(self, name):
        Config(apiurl, name)
        self.api = StagingAPI(apiurl, name)
        self.staging_projects = dict()
        self.listener = None
        for p in self.api.get_staging_projects():
            if self.api.is_adi_project(p):
                continue
            self.staging_projects[p] = self.initial_staging_state(p)
        print(self.staging_projects)

    def staging_letter(self, name):
        return name.split(':')[-1]

    def map_iso(self, staging_project, iso):
        raise 'Unimplemented'

    def gather_isos(self, name, repository):
        url = self.api.makeurl(['published', name, repository, 'iso'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        ret = []
        for entry in root.findall('entry'):
            if entry.get('name').endswith('iso'):
                ret.append(self.map_iso(name, entry.get('name')))
        return ret

    def gather_buildid(self, name, repository):
        url = self.api.makeurl(['published', name, repository], {'view': 'status'})
        f = self.api.retried_GET(url)
        id = ET.parse(f).getroot().find('buildid')
        if id is not None:
            return id.text

    def initial_staging_state(self, name):
        return {'isos': self.gather_isos(name, 'images'),
                'id': self.gather_buildid(name, 'images')}

    # once the project is added to a listener, it's calling back
    def fetch_initial_openqa(self, listener):
        self.listener = listener
        for project in self.staging_projects:
            self.update_staging_status(project)

    def fetch_openqa_jobs(self, staging, iso):
        buildid = self.staging_projects[staging].get('id')
        if not buildid:
            print("I don't know the build id of " + staging)
            return
        openqa = self.listener.jobs_for_iso(iso)
        for job in openqa:
            print(staging, iso, job['id'], job['state'], job['result'],
                  job['settings']['MACHINE'], job['settings']['TEST'])
            xml = self.openqa_check_xml(self.listener.test_url(job),
                                        self.map_openqa_result(job),
                                        job['settings']['TEST'] + '@' + job['settings']['MACHINE'])
            url = self.api.makeurl(['status_reports', 'published', staging, 'images', 'reports', buildid])
            try:
                http_POST(url, data=xml)
            except HTTPError:
                # https://github.com/openSUSE/open-build-service/issues/6051
                print('failed to post status to ' + url)
                print(xml)

    def update_staging_status(self, project):
        for iso in self.staging_projects[project]['isos']:
            self.fetch_openqa_jobs(project, iso)

    def update_staging_buildid(self, project, repository, buildid):
        self.staging_projects[project]['id'] = buildid
        self.staging_projects[project]['isos'] = self.gather_isos(project, repository)
        self.update_staging_status(project)

    def check_published_repo(self, project, repository, buildid):
        if repository != 'images':
            return
        for p in self.staging_projects:
            if project == p:
                self.update_staging_buildid(project, repository, buildid)

    def matching_project(self, iso):
        for p in self.staging_projects:
            if iso in self.staging_projects[p]['isos']:
                return p

    def map_openqa_result(self, job):
        if job['result'] in ['passed', 'softfailed']:
            return 'success'
        if job['result'] == 'none':
            return 'pending'
        return 'failure'

    def openqa_job_change(self, iso):
        staging = self.matching_project(iso)
        if not staging:
            return
        # we fetch all openqa jobs so we can avoid long job names
        self.fetch_openqa_jobs(staging, iso)

    def openqa_check_xml(self, url, state, name):
        check = ET.Element('check')
        se = ET.SubElement(check, 'url')
        se.text = url
        se = ET.SubElement(check, 'state')
        se.text = state
        se = ET.SubElement(check, 'name')
        se.text = name
        return ET.tostring(check)

class Listener(object):
    def __init__(self, amqp_prefix, amqp_url, openqa_url):
        self.projects = []
        self.amqp_prefix = amqp_prefix
        self.amqp_url = amqp_url
        self.openqa_url = openqa_url
        self.openqa = OpenQA_Client(server=openqa_url)
        self.setup_rabbitmq()

    def setup_rabbitmq(self):
        connection = pika.BlockingConnection(pika.URLParameters(self.amqp_url))
        self.channel = connection.channel()

        self.channel.exchange_declare(exchange='pubsub', exchange_type='topic', passive=True, durable=True)

        result = self.channel.queue_declare(exclusive=True)
        queue_name = result.method.queue

        for event in ['.obs.repo.published', '.openqa.job.done',
                      '.openqa.job.create', '.openqa.job.restart']:
            self.channel.queue_bind(exchange='pubsub',
                                    queue=queue_name,
                                    routing_key=self.amqp_prefix + event)
        self.channel.basic_consume(self.on_message,
                                   queue=queue_name,
                                   no_ack=True)

    def add(self, project):
        project.fetch_initial_openqa(self)
        self.projects.append(project)

    def jobs_for_iso(self, iso):
        values = {
            'iso': iso,
            'scope': 'current',
            'latest': '1',
        }
        return self.openqa.openqa_request('GET', 'jobs', values)['jobs']

    def get_step_url(self, testurl, modulename):
        failurl = testurl + '/modules/{!s}/fails'.format(quote_plus(modulename))
        fails = requests.get(failurl).json()
        failed_step = fails.get('first_failed_step', 1)
        return "{!s}#step/{!s}/{:d}".format(testurl, modulename, failed_step)

    def test_url(self, job):
        url = self.openqa_url + ("/tests/%d" % job['id'])
        if job['result'] == 'failed':
            for module in job['modules']:
                if module['result'] == 'failed':
                    return self.get_step_url(url, module['name'])
        return url

    def on_published_repo(self, payload):
        for p in self.projects:
            p.check_published_repo(str(payload['project']), str(payload['repo']), str(payload['buildid']))

    def on_openqa_job(self, iso):
        print('openqa_job_change', iso)
        for p in self.projects:
            p.openqa_job_change(iso)

    def on_message(self, unused_channel, method, properties, body):
        if method.routing_key == '{}.obs.repo.published'.format(amqp_prefix):
            self.on_published_repo(json.loads(body))

        if method.routing_key == '{}.openqa.job.done'.format(amqp_prefix):
            self.on_openqa_job(json.loads(body).get('ISO'))
        if method.routing_key == '{}.openqa.job.create'.format(amqp_prefix):
            self.on_openqa_job(json.loads(body).get('ISO'))
        if method.routing_key == '{}.openqa.job.restart'.format(amqp_prefix):
            self.on_openqa_job(json.loads(body).get('ISO'))

    def listen(self):
        print(' [*] Waiting for logs. To exit press CTRL+C')
        self.channel.start_consuming()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Bot to sync openQA status to OBS')
    parser.add_argument("--apiurl", '-A', type=str, default='https://api.opensuse.org', help='API URL of OBS')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project letter')
    parser.add_argument('-f', '--force', action='store_true', default=False,
                        help='force the write of the comment')
    parser.add_argument('-p', '--project', type=str, default='Factory',
                        help='openSUSE version to make the check (Factory, 13.2)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config()
    osc.conf.config['debug'] = args.debug

    apiurl = args.apiurl

    if apiurl.endswith('suse.de'):
        amqp_prefix = 'suse'
        amqp_url = "amqps://suse:suse@rabbit.suse.de?heartbeat_interval=15"
        openqa_url = 'https://openqa.suse.de'
    else:
        amqp_prefix = 'opensuse'
        amqp_url = "amqps://opensuse:opensuse@rabbit.opensuse.org?heartbeat_interval=15"
        openqa_url = 'https://openqa.opensuse.org'

    l = Listener(amqp_prefix, amqp_url, openqa_url)

    if amqp_prefix == 'opensuse':

        class Leap15(Project):
            def map_iso(self, project, iso):
                # B: openSUSE-Leap-15.1-DVD-x86_64-Build21.3-Media.iso
                # A: openSUSE-Leap:15.1-Staging:D-Staging-DVD-x86_64-Build21.3-Media.iso
                return re.sub(r'Leap-(.*)-DVD', r'Leap:\1-Staging:' + self.staging_letter(project) + '-Staging-DVD', iso)

        l.add(Leap15('openSUSE:Leap:15.1'))
    else:
        class Sle15(Project):
            def map_iso(self, project, iso):
                # B: SLE-15-SP1-Installer-DVD-x86_64-Build67.2-Media1.iso
                # A: SLE-15-SP1-Staging:D-Installer-DVD-x86_64-BuildD.67.2-Media1.iso
                letter = self.staging_letter(project)
                begin = re.sub(r'^(.*)-Installer.*', r'\1', iso)
                middle = re.sub(r'^.*-(Installer.*-Build).*', r'\1', iso)
                ending = re.sub(r'.*-Build', '', iso)
                return "%s-Staging:%s-%s%s.%s" % (begin, letter, middle, letter, ending)

        l.add(Sle15('SUSE:SLE-15-SP1:GA'))

        class Sle12(Project):
            def map_iso(self, project, iso):
                # B: Test-Server-DVD-x86_64-Build42.1-Media.iso
                # A: SLE12-SP4-Staging:Y-Test-Server-DVD-x86_64-BuildY.42.1-Media.iso
                letter = self.staging_letter(project)
                begin = re.sub(r'SUSE:SLE-(.*):GA.*', r'SLE\1', project)
                middle = re.sub(r'^(.*-Build).*', r'\1', iso)
                ending = re.sub(r'.*-Build', '', iso)
                return "%s-Staging:%s-%s%s.%s" % (begin, letter, middle, letter, ending)

        l.add(Sle12('SUSE:SLE-12-SP4:GA'))

    l.listen()
