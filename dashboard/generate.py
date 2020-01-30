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

    def add(self, name, nick):
        # cyclic dependency!
        self.projects.append(Project(self, name, nick))

    def build_summary(self, project, repository):
        url = makeurl(self.apiurl, ['build', project, '_result'], { 'repository': repository, 'view': 'summary' })
        f = http_GET(url)
        root = ET.parse(f).getroot()
        failed = 0
        unresolvable = 0
        building = 0
        succeeded = 0
        for result in root.findall('.//statuscount'):
            code = result.get('code')
            count = int(result.get('count'))
            if code == 'excluded' or code == 'disabled':
                continue # ignore
            if code == 'succeeded':
                succeeded += count
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
        return { 'building': 1000 - int(building * 1000 / (building + failed + succeeded)),
                 'failed': failed,
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
    def __init__(self, fetcher, name, nick):
        self.fetcher = fetcher
        self.name = name
        self.nick = nick
        self.all_archs = fetcher.generate_all_archs(name)
        self.ttm_status = fetcher.fetch_ttm_status(name)
        self.ttm_version = fetcher.fetch_product_version(name)

    def standard_progress(self):
        return fetcher.build_summary(self.name, 'standard')

    def images_progress(self):
        try:
            return fetcher.build_summary(self.name, 'images')
        except HTTPError as e:
            print(f"failed to fetch images for {self.name}", file=sys.stderr)
            return {'building': -1}

    def all_archs(self):
        self.all_archs

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

    fetcher.add('openSUSE:Factory', 'Factory')
    fetcher.add('openSUSE:Factory:Rings:0-Bootstrap', 'Ring 0')
    fetcher.add('openSUSE:Factory:Rings:1-MinimalX', 'Ring 1')
    fetcher.add('openSUSE:Factory:ARM', 'ARM')
    fetcher.add('openSUSE:Factory:PowerPC', 'Power')
    fetcher.add('openSUSE:Factory:zSystems', 'System Z')
    fetcher.add('openSUSE:Factory:RISCV', 'Risc V')

    with app.app_context():
        rendered = render_template('dashboard.html',
            projectname = args.project,
            projects = fetcher.projects)
        print(rendered)
