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

class Project(object):
    def __init__(self, name):
        Config(apiurl, name)
        self.api = StagingAPI(apiurl, name)
        self.staging_projects = dict()
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

    def initial_staging_state(self, name):
        ret = {'isos': self.gather_isos(name, 'images')}
        # missing API for initial repo id
        return ret

    def update_staging_buildid(self, project, repository, buildid):
        self.staging_projects[project]['id'] = buildid
        self.staging_projects[project]['isos'] = self.gather_isos(project, repository)
        print('UPDATE', project, self.staging_projects[project])

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

    def map_openqa_result(self, result):
        if result in ['passed', 'softfailed']:
            return 'success'
        return 'failure'

    def openqa_done(self, iso, test, machine, id, result):
        print('openqa_done', iso, test, machine, id, result)
        staging = self.matching_project(iso)
        if not staging:
            return
        buildid = self.staging_projects[staging].get('id')
        if not buildid:
            print("I don't know the build id of " + staging)
            return
        xml = self.openqa_check_xml(id,
                                    self.map_openqa_result(result),
                                    test + '@' + machine)
        url = self.api.makeurl(['status_reports', 'published', staging, 'images', 'reports', buildid])
        http_POST(url, data=xml)
    
    def openqa_create(self, iso, test, machine, id):
        print('openqa_create', iso, test, machine, id)

    def openqa_check_xml(self, id, state, name):
        check = ET.Element('check')
        se = ET.SubElement(check, 'url')
        se.text = "https://openqa.suse.de/tests/{}".format(id)
        se = ET.SubElement(check, 'state')
        se.text = state
        se = ET.SubElement(check, 'name')
        se.text = name
        return ET.tostring(check)

class Listener(object):
    def __init__(self, amqp_prefix, amqp_url):
        self.projects = []
        self.amqp_prefix = amqp_prefix
        self.amqp_url = amqp_url
        connection = pika.BlockingConnection(pika.URLParameters(amqp_url))
        self.channel = connection.channel()

        self.channel.exchange_declare(exchange='pubsub', exchange_type='topic', passive=True, durable=True)

        result = self.channel.queue_declare(exclusive=True)
        queue_name = result.method.queue

        self.channel.queue_bind(exchange='pubsub',
                                queue=queue_name,routing_key='#')
        self.channel.basic_consume(self.on_message,
                                   queue=queue_name,
                                no_ack=True)

        print(' [*] Waiting for logs. To exit press CTRL+C')

    def add(self, project):
        self.projects.append(project)

    def on_published_repo(self, payload):
        for p in self.projects:
            p.check_published_repo(str(payload['project']), str(payload['repo']), str(payload['buildid']))

    def on_openqa_create(self, payload):
        for p in self.projects:
            p.openqa_create(str(payload.get('ISO', '')), str(payload['TEST']), str(payload['MACHINE']), str(payload['id']))

    def on_openqa_restart(self, payload):
        print(payload)

    def on_openqa_done(self, payload):
        for p in self.projects:
            p.openqa_done(str(payload.get('ISO', '')), payload['TEST'], payload['MACHINE'], payload['id'], payload['result'])

    def on_message(self, unused_channel, method, properties, body):
        if method.routing_key == '{}.obs.repo.published'.format(amqp_prefix):
            self.on_published_repo(json.loads(body))

        if method.routing_key == '{}.openqa.job.done'.format(amqp_prefix):
            self.on_openqa_done(json.loads(body))
        if method.routing_key == '{}.openqa.job.create'.format(amqp_prefix):
            self.on_openqa_create(json.loads(body))
        if method.routing_key == '{}.openqa.job.restart'.format(amqp_prefix):
            self.on_openqa_restart(json.loads(body))

    def listen(self):
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
    else:
        amqp_prefix = 'opensuse'
        amqp_url = "amqps://opensuse:opensuse@rabbit.opensuse.org?heartbeat_interval=15"

    l = Listener(amqp_prefix, amqp_url)
    if amqp_prefix == 'opensuse':

        class Leap15(Project):
            def __init__(self, name):
                super(name)

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
