#!/usr/bin/python

import argparse
import logging
import pika
import sys
import json
import osc
import re
from time import sleep
from osc.core import http_GET, http_POST, makeurl
from M2Crypto.SSL import SSLError as SSLError
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from lxml import etree as ET
from openqa_client.client import OpenQA_Client
from openqa_client.exceptions import ConnectionError
try:
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote_plus
except ImportError:
    #python 2.x
    from urllib2 import HTTPError, URLError
    from urllib import quote_plus

import requests
from PubSubConsumer import PubSubConsumer


class Project(object):
    def __init__(self, name):
        self.name = name
        Config(apiurl, name)
        self.api = StagingAPI(apiurl, name)
        self.staging_projects = dict()
        self.listener = None
        self.logger = logging.getLogger(__name__)
        self.replace_string = self.api.attribute_value_load('OpenQAMapping')

    def init(self):
        for p in self.api.get_staging_projects():
            if self.api.is_adi_project(p):
                continue
            self.staging_projects[p] = self.initial_staging_state(p)
            self.update_staging_status(p)

    def staging_letter(self, name):
        return name.split(':')[-1]

    def map_iso(self, staging_project, iso):
        parts = self.replace_string.split('/')
        if parts[0] != 's':
            raise Exception("{}'s iso_replace_string does not start with s/".format(self.name))
        old = parts[1]
        new = parts[2]
        new = new.replace('$LETTER', self.staging_letter(staging_project))
        return re.compile(old).sub(new, iso)

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

    def fetch_openqa_jobs(self, staging, iso):
        buildid = self.staging_projects[staging].get('id')
        if not buildid:
            self.logger.info("I don't know the build id of " + staging)
            return
        # all openQA jobs are created at the same URL
        url = self.api.makeurl(['status_reports', 'published', staging, 'images', 'reports', buildid])
        openqa = self.listener.jobs_for_iso(iso)
        # collect job infos to pick names
        openqa_infos = dict()
        for job in openqa:
            print(staging, iso, job['id'], job['state'], job['result'],
                  job['settings']['MACHINE'], job['settings']['TEST'])
            openqa_infos[job['id']] = {'url': self.listener.test_url(job)}
            openqa_infos[job['id']]['state'] = self.map_openqa_result(job)
            openqa_infos[job['id']]['name'] = job['settings']['TEST']
            openqa_infos[job['id']]['machine'] = job['settings']['MACHINE']

        # make sure the names are unique
        taken_names = dict()
        for id in openqa_infos:
            name = openqa_infos[id]['name']
            if name in taken_names:
                openqa_infos[id]['name'] = openqa_infos[id]['name'] + "@" + openqa_infos[id]['machine']
                # the other id
                id = taken_names[name]
                openqa_infos[id]['name'] = openqa_infos[id]['name'] + "@" + openqa_infos[id]['machine']
            taken_names[name] = id

        for info in openqa_infos.values():
            xml = self.openqa_check_xml(info['url'], info['state'], 'openqa:' + info['name'])
            try:
                http_POST(url, data=xml)
            except HTTPError:
                self.logger.error('failed to post status to ' + url)

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


class Listener(PubSubConsumer):
    def __init__(self, amqp_prefix, amqp_url, openqa_url):
        super(Listener, self).__init__(amqp_url, logging.getLogger(__name__))
        self.projects = []
        self.amqp_prefix = amqp_prefix
        self.openqa_url = openqa_url
        self.openqa = OpenQA_Client(server=openqa_url)

    def routing_keys(self):
        ret = []
        for suffix in ['.obs.repo.published', '.openqa.job.done',
                       '.openqa.job.create', '.openqa.job.restart']:
            ret.append(self.amqp_prefix + suffix)
        return ret

    def add(self, project):
        project.listener = self
        self.projects.append(project)

    def start_consuming(self):
        # now we are (re-)connected to the bus and need to fetch the
        # initial state
        for project in self.projects:
            self.logger.info('Fetching ISOs of %s', project.name)
            project.init()
        self.logger.info('Finished fetching initial ISOs, listening')
        super(Listener, self).start_consuming()

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
        self.logger.debug('openqa_job_change', iso)
        for p in self.projects:
            p.openqa_job_change(iso)

    def on_message(self, unused_channel, method, properties, body):
        if method.routing_key == '{}.obs.repo.published'.format(amqp_prefix):
            self.on_published_repo(json.loads(body))
        elif re.search(r'.openqa.', method.routing_key):
            self.on_openqa_job(json.loads(body).get('ISO'))
        else:
            self.logger.warning("unknown rabbitmq message {}".format(method.routing_key))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Bot to sync openQA status to OBS')
    parser.add_argument("--apiurl", '-A', type=str, help='API URL of OBS')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project letter')
    parser.add_argument('-f', '--force', action='store_true', default=False,
                        help='force the write of the comment')
    parser.add_argument('-p', '--project', type=str, default='Factory',
                        help='openSUSE version to make the check (Factory, 13.2)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl = args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']

    if apiurl.endswith('suse.de'):
        amqp_prefix = 'suse'
        amqp_url = "amqps://suse:suse@rabbit.suse.de"
        openqa_url = 'https://openqa.suse.de'
    else:
        amqp_prefix = 'opensuse'
        amqp_url = "amqps://opensuse:opensuse@rabbit.opensuse.org"
        openqa_url = 'https://openqa.opensuse.org'

    logging.basicConfig(level=logging.INFO)

    l = Listener(amqp_prefix, amqp_url, openqa_url)
    url = makeurl(apiurl, ['search', 'project', 'id'], {'match': 'attribute/@name="OSRT:OpenQAMapping"'})
    f = http_GET(url)
    root = ET.parse(f).getroot()
    for entry in root.findall('project'):
        l.add(Project(entry.get('name')))

    try:
        l.run()
    except KeyboardInterrupt:
        l.stop()
