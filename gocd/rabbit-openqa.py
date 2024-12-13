#!/usr/bin/python3

import argparse
import logging

import json
import osc
import re
from osc.core import http_GET, http_POST, makeurl
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from lxml import etree as ET
from openqa_client.client import OpenQA_Client
from packaging import version
from urllib.error import HTTPError
from urllib.parse import quote_plus

import requests
from osclib.PubSubConsumer import PubSubConsumer


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
        projects = set()
        for project in self.api.get_staging_projects():
            if self.api.is_adi_project(project):
                continue
            self.staging_projects[project] = self.initial_staging_state(project)
            projects.add(project)
        return projects

    def staging_letter(self, name):
        return name.split(':')[-1]

    def map_iso(self, staging_project, iso):
        parts = self.replace_string.split('/')
        if parts[0] != 's':
            raise Exception(f"{self.name}'s iso_replace_string does not start with s/")
        old = parts[1]
        new = parts[2]
        new = new.replace('$LETTER', self.staging_letter(staging_project))
        try:
            stagingiso = re.compile(old).sub(new, iso)
        except re.error:
            self.logger.error(f"_MAP_ISO {self.replace_string} does not create valid regexps in {self.name}")
            return None

        if stagingiso == iso:
            self.logger.info(f"{self.replace_string} did not map {iso} properly, ignoring")
            return None

        return stagingiso

    def gather_isos(self, name, repository):
        iso_set = set()

        # Look for .iso and other images/sbom assets in the repository root and in the
        # iso sub-folder of /published/prj/repo/
        places = (
            ['published', name, repository, 'iso'],
            ['published', name, repository],
        )
        for place in places:
            url = self.api.makeurl(place)
            f = self.api.retried_GET(url)
            root = ET.parse(f).getroot()

            for entry in root.findall('entry'):
                filename = entry.get('name')
                if (filename.endswith('.qcow2') or
                        filename.endswith('.raw.xz') or
                        filename.endswith('.spdx.json') or
                        filename.endswith('.iso')):
                    iso_set.add(self.map_iso(name, filename))

        # Filter out isos which couldn't be mapped
        return [iso for iso in iso_set if iso]

    def gather_buildid(self, name, repository):
        url = self.api.makeurl(['published', name, repository], {'view': 'status'})
        f = self.api.retried_GET(url)
        id = ET.parse(f).getroot().find('buildid')
        if id is not None:
            return id.text

    def initial_staging_state(self, name):
        return {'isos': self.gather_isos(name, 'images'),
                'id': self.gather_buildid(name, 'images')}

    def fetch_openqa_jobs(self, staging, iso, openqa_infos):
        openqa = self.listener.jobs_for_iso(iso)
        # collect job infos to pick names
        for job in openqa:
            print(staging, iso, job['id'], job['state'], job['result'],
                  job['settings']['FLAVOR'], job['settings']['TEST'], job['settings']['MACHINE'])
            openqa_infos[job['id']] = {'url': self.listener.test_url(job)}
            openqa_infos[job['id']]['state'] = self.map_openqa_result(job)
            openqa_infos[job['id']]['build'] = job['settings']['BUILD']
            openqa_infos[job['id']]['name'] = f"{job['settings']['FLAVOR']}-{job['settings']['TEST']}@{job['settings']['MACHINE']}"

    def compare_simple_builds(build1, build2):
        """Simple build number comparison"""
        ver1 = version.parse(build1)
        ver2 = version.parse(build2)
        if ver1 < ver2:
            return -1
        if ver1 > ver2:
            return 1
        return 0

    def compare_composite_builds(build1, build2):
        """Compare BUILD numbers consisting of multiple _-separated components."""
        components1 = build1.split('_')
        components2 = build2.split('_')
        if len(components1) != len(components2):
            raise Exception(f'Failed to compare {build1} and {build2}: Different format')

        component_cmps = [Project.compare_simple_builds(components1[i], components2[i]) for i in range(0, len(components1))]
        less = -1 in component_cmps
        greater = 1 in component_cmps
        if less and greater:
            raise Exception(f'Failed to compare {build1} and {build2}: Not ordered')
        if less:
            return -1
        if greater:
            return 1
        return 0

    def update_staging_status(self, staging):
        openqa_infos = dict()
        for iso in self.staging_projects[staging]['isos']:
            self.fetch_openqa_jobs(staging, iso, openqa_infos)

        buildid = self.staging_projects[staging].get('id')
        if not buildid:
            self.logger.info("I don't know the build id of " + staging)
            return
        # all openQA jobs are created at the same URL
        url = self.api.makeurl(['status_reports', 'published', staging, 'images', 'reports', buildid])

        # make sure the names are unique
        obsolete_jobs = []
        taken_names = dict()
        for id in openqa_infos:
            name = openqa_infos[id]['name']
            if name in taken_names:
                # There are multiple jobs with that specific FLAVOR-TEST@MACHINE.
                # In SLE Micro, jobs currently use BUILD=(dvdbuild)_(image_build),
                # so if the dvd is rebuilt, new image jobs are triggered for the
                # same binary. The openQA ?latest=1 filter doesn't look at that,
                # so we have to figure out which of those is the most recent one.
                build1 = openqa_infos[taken_names[name]]['build']
                build2 = openqa_infos[id]['build']
                if '_' in build1 and '_' in build2 and build1 != build2:
                    # Use the more recent build
                    buildcmp = Project.compare_composite_builds(build1, build2)
                    self.logger.info(f'Multiple builds for {name}, {build1} and {build2}. Comparison: {buildcmp}')
                    if buildcmp < 0:  # Drop the previous one
                        obsolete_jobs.append(taken_names[name])
                        taken_names[name] = id
                        continue
                    elif buildcmp > 0:  # Drop this one
                        obsolete_jobs.append(id)
                        continue

                raise Exception(f'Names of job #{id} and #{taken_names[name]} collide: {name}')
            taken_names[name] = id

        for id in obsolete_jobs:
            del openqa_infos[id]

        for info in openqa_infos.values():
            xml = self.openqa_check_xml(info['url'], info['state'], 'openqa:' + info['name'])
            try:
                if self.listener.dryrun:
                    print(f"Would POST to {url}: {xml}")
                else:
                    http_POST(url, data=xml)
            except HTTPError:
                self.logger.error('failed to post status to ' + url)

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
        self.update_staging_status(staging)

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
    def __init__(self, amqp_prefix, openqa_url, dryrun):
        super(Listener, self).__init__(amqp_prefix, logging.getLogger(__name__))
        self.projects = []
        self.amqp_prefix = amqp_prefix
        self.openqa_url = openqa_url
        self.dryrun = dryrun
        self.openqa = OpenQA_Client(server=openqa_url)
        self.projects_to_check = set()

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
        self.projects_to_check = set()
        for project in self.projects:
            try:
                self.logger.info('Fetching ISOs of %s', project.name)
                for sproj in project.init():
                    self.projects_to_check.add((project, sproj))
            except HTTPError as e:
                if e.code == 404:
                    # No staging workflow? Have to protect against "rogue" projects
                    self.logger.error('Failed to load staging projects')
                    continue
                else:
                    raise

        self.logger.info('Finished fetching initial ISOs, listening')
        super(Listener, self).start_consuming()

    def interval(self):
        if len(self.projects_to_check):
            return 5
        return super(Listener, self).interval()

    def check_some_projects(self):
        count = 0
        limit = 5
        while len(self.projects_to_check):
            project, staging = self.projects_to_check.pop()
            project.update_staging_status(staging)
            count += 1
            if count >= limit:
                return

    def still_alive(self):
        self.check_some_projects()
        super(Listener, self).still_alive()

    def is_production_job(self, job):
        if '/' in job['settings'].get('BUILD', '/') or \
           'group' not in job or 'Development' in job['group']:
            return False

        return True

    def jobs_for_iso(self, iso):
        # Try ISO= matching first
        values = {
            'iso': iso,
            'scope': 'current',
            'latest': '1',
        }
        jobs = self.openqa.openqa_request('GET', 'jobs', values)['jobs']

        # If no matches, try HDD_1=
        if len(jobs) == 0:
            del values['iso']
            values['hdd_1'] = iso
            jobs = self.openqa.openqa_request('GET', 'jobs', values)['jobs']

        # Ignore PR verification runs (and jobs without 'BUILD')
        return [job for job in jobs if self.is_production_job(job)]

    def get_step_url(self, testurl, modulename):
        failurl = testurl + f'/modules/{quote_plus(modulename)!s}/fails'
        fails = requests.get(failurl).json()
        failed_step = fails.get('first_failed_step', 1)
        return f"{testurl!s}#step/{modulename!s}/{failed_step:d}"

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
        self.logger.debug('openqa_job_change %s', iso)
        for p in self.projects:
            p.openqa_job_change(iso)

    def on_message(self, unused_channel, method, properties, body):
        self.acknowledge_message(method.delivery_tag)
        if method.routing_key == f'{amqp_prefix}.obs.repo.published':
            self.on_published_repo(json.loads(body))
        elif re.search(r'.openqa.', method.routing_key):
            data = json.loads(body)
            if '/' in data.get('BUILD'):
                return  # Ignore PR verification runs
            if data.get('ISO'):
                self.on_openqa_job(data.get('ISO'))
            elif data.get('HDD_1'):
                self.on_openqa_job(data.get('HDD_1'))
        else:
            self.logger.warning(f"unknown rabbitmq message {method.routing_key}")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Bot to sync openQA status to OBS')
    parser.add_argument("--apiurl", '-A', type=str, help='API URL of OBS')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')
    parser.add_argument('--dry', action='store_true', default=False,
                        help='do not perform changes')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']

    if apiurl.endswith('suse.de'):
        amqp_prefix = 'suse'
        openqa_url = 'https://openqa.suse.de'
    else:
        amqp_prefix = 'opensuse'
        openqa_url = 'https://openqa.opensuse.org'

    logging.basicConfig(level=logging.INFO)

    listener = Listener(amqp_prefix, openqa_url, dryrun=args.dry)
    url = makeurl(apiurl, ['search', 'project', 'id'], {'match': 'attribute/@name="OSRT:OpenQAMapping"'})
    f = http_GET(url)
    root = ET.parse(f).getroot()
    for entry in root.findall('project'):
        listener.add(Project(entry.get('name')))

    try:
        listener.run(runtime=10800)
    except KeyboardInterrupt:
        listener.stop()
