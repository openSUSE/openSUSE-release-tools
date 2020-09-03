#!/usr/bin/python3

import argparse
import logging
import pika
import sys
import json
import osc
import re
import yaml
from time import sleep
from osc.core import http_GET, http_POST, makeurl, show_project_meta
from M2Crypto.SSL import SSLError as SSLError
from osclib.conf import Config
from osclib.core import attribute_value_load
from osclib.stagingapi import StagingAPI
from lxml import etree as ET
from openqa_client.client import OpenQA_Client
from openqa_client.exceptions import ConnectionError, RequestError
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from datetime import datetime, timezone

import requests
from osclib.PubSubConsumer import PubSubConsumer
from flask import Flask, render_template

class Fetcher(object):
    def __init__(self, apiurl, opts):
        self.projects = []
        self.opts = opts
        self.apiurl = apiurl
        if apiurl.endswith('suse.de'):
            amqp_prefix = 'suse'
            openqa_url = 'https://openqa.suse.de'
        else:
            amqp_prefix = 'opensuse'
            openqa_url = 'https://openqa.opensuse.org'
        self.openqa = OpenQA_Client(openqa_url)

    def openqa_results(self, openqa_group, snapshot):
        jobs = {}
        if not openqa_group or not snapshot:
            return jobs
        result = self.openqa.openqa_request('GET', 'jobs', {'groupid': openqa_group, 'build': snapshot, 'latest': 1})
        for job in result['jobs']:
            if job['clone_id'] or job['result'] == 'obsoleted':
                continue
            name = job['name'].replace(snapshot, '')
            key = job['result']
            if job['state'] != 'done':
                key = job['state']
                if key == 'uploading' or key == 'assigned':
                    key = 'running'
            jobs.setdefault(key, []).append(job['name'])
        return jobs

    def add(self, name, **kwargs):
        # cyclic dependency!
        self.projects.append(Project(self, name, kwargs))

    def build_summary(self, project, repository):
        url = makeurl(self.apiurl, ['build', project, '_result'], { 'repository': repository, 'view': 'summary' })
        try:
            f = http_GET(url)
        except HTTPError as e:
            return { 'building': -1 }
        root = ET.parse(f).getroot()
        failed = 0
        unresolvable = 0
        building = 0
        succeeded = 0
        broken = 0
        for result in root.findall('.//statuscount'):
            code = result.get('code')
            count = int(result.get('count'))
            if code == 'excluded' or code == 'disabled' or code == 'locked':
                continue # ignore
            if code == 'succeeded':
                succeeded += count
                continue
            if code == 'broken':
                broken += count
                continue
            if code == "failed":
                failed += count
                continue
            if code == "unresolvable":
                unresolvable += count
                continue
            building += count
            #print(code, file=sys.stderr)
        # let's count them as building
        if building > 0:
            building += unresolvable
            unresolvable = 0
        if building + failed + succeeded == 0:
            return {'building': -1}
        return { 'building': 1000 - int(building * 1000 / (building + failed + succeeded + broken)),
                 'failed': failed,
                 'broken': broken,
                 'unresolvable': unresolvable }

    def generate_all_archs(self, project):
        meta = ET.fromstringlist(show_project_meta(self.apiurl, project))
        archs = set()
        for arch in meta.findall('.//arch'):
            archs.add(arch.text)
        result = []
        for arch in archs:
            result.append(f"arch_{arch}=1")
        return '&'.join(result)

    def fetch_ttm_status(self, project):
        text = attribute_value_load(self.apiurl, project, 'ToTestManagerStatus')
        if text:
            return yaml.safe_load(text)
        return dict()

    def fetch_product_version(self, project):
        return attribute_value_load(self.apiurl, project, 'ProductVersion')

class Project(object):
    def __init__(self, fetcher, name, kwargs):
        self.fetcher = fetcher
        self.name = name
        self.nick = kwargs.get('nick')
        self.openqa_version = kwargs.get('openqa_version')
        self.openqa_group = kwargs.get('openqa_group')
        self.openqa_id = kwargs.get('openqa_groupid')
        self.download_url = kwargs.get('download_url')
        self.all_archs = fetcher.generate_all_archs(name)
        self.ttm_status = fetcher.fetch_ttm_status(name)
        self.ttm_version = fetcher.fetch_product_version(name)

    def build_summary(self, repo):
        return fetcher.build_summary(self.name, repo)

    def all_archs(self):
        self.all_archs

    def openqa_summary(self):
        return self.fetcher.openqa_results(self.openqa_id, self.ttm_status.get('testing'))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Bot to sync openQA status to OBS')
    parser.add_argument("--apiurl", '-A', type=str, help='API URL of OBS')
    parser.add_argument('-p', '--project', type=str, default='Factory',
                        help='openSUSE version to make the check (Factory, 15.2)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl = args.apiurl)
    osc.conf.config['debug'] = args.debug
    apiurl = osc.conf.config['apiurl']

    fetcher = Fetcher(apiurl, args)
    logging.basicConfig(level=logging.INFO)

    app = Flask(__name__)

    fetcher.add('openSUSE:Factory', nick='Factory', download_url='https://download.opensuse.org/tumbleweed/iso/', openqa_group='openSUSE Tumbleweed', openqa_version='Tumbleweed', openqa_groupid=1)
    fetcher.add('openSUSE:Factory:Live', nick='Live')
    fetcher.add('openSUSE:Factory:Rings:0-Bootstrap', nick='Ring 0')
    fetcher.add('openSUSE:Factory:Rings:1-MinimalX', nick='Ring 1')
    fetcher.add('openSUSE:Factory:ARM', nick='ARM', download_url='http://download.opensuse.org/ports/aarch64/tumbleweed/iso/', openqa_group='openSUSE Tumbleweed AArch64', openqa_version='Tumbleweed', openqa_groupid=3)
    fetcher.add('openSUSE:Factory:ARM:Live', nick='ARM Live')
    fetcher.add('openSUSE:Factory:ARM:Rings:0-Bootstrap', nick='ARM Ring 0')
    fetcher.add('openSUSE:Factory:ARM:Rings:1-MinimalX', nick='ARM Ring 1')
    fetcher.add('openSUSE:Factory:PowerPC', nick='Power', download_url='http://download.opensuse.org/ports/ppc/tumbleweed/iso/', openqa_group='openSUSE Tumbleweed PowerPC', openqa_version='Tumbleweed', openqa_groupid=4)
    fetcher.add('openSUSE:Factory:zSystems', nick='System Z', download_url='http://download.opensuse.org/ports/zsystems/tumbleweed/iso/', openqa_group='openSUSE Tumbleweed s390x', openqa_version='Tumbleweed', openqa_groupid=34)
    fetcher.add('openSUSE:Factory:RISCV', nick='Risc V', download_url='http://download.opensuse.org/ports/riscv/tumbleweed/iso/')

    with app.app_context():
        rendered = render_template('dashboard.html',
            projectname = args.project,
            lastupdate = datetime.now(timezone.utc),
            projects = fetcher.projects)
        print(rendered)
