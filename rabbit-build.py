#!/usr/bin/python

from __future__ import print_function

import argparse
import datetime
import json
import logging
import os
import sys

import osc
from osc.core import http_GET, http_POST, makeurl

from osclib.core import target_archs
from lxml import etree as ET
try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

from PubSubConsumer import PubSubConsumer


class Listener(PubSubConsumer):
    def __init__(self, apiurl, amqp_prefix, project, repository):
        super(Listener, self).__init__(amqp_prefix, logging.getLogger(__name__))
        self.apiurl = apiurl
        self.project = project
        self.repository = repository
        self.archs = None
        self.amqp_prefix = amqp_prefix
        self.timer_id = None

    def routing_keys(self):
        ret = []
        for suffix in ['.obs.package.build_fail', '.obs.package.build_success',
                       '.obs.package.build_unchanged', '.obs.repo.build_finished']:
            ret.append(self.amqp_prefix + suffix)
        return ret

    def restart_timer(self):
        interval = 30
        if self.timer_id:
            self._connection.remove_timeout(self.timer_id)
        else:
            # check the initial state on first timer hit
            # so be quick about it
            interval = 0
        self.timer_id = self._connection.add_timeout(
            interval, self.still_alive)

    def check_failures(self):
        url = makeurl(self.apiurl, ['build', self.project, '_result'],
                      {'view': 'summary', 'repository': self.repository})
        root = ET.parse(http_GET(url)).getroot()
        for count in root.findall('.//statuscount'):
            if int(count.get('count', 0)) == 0:
                continue
            if count.get('code') in ['succeeded', 'excluded', 'disabled']:
                continue
            print(ET.tostring(count))
            sys.exit(1)

    def still_alive(self):
        if not self.archs:
            self.archs = target_archs(self.apiurl, self.project, self.repository)
            # initial check
            if self.check_all_archs():
                self.stop()
                return

        # https://gitlab.com/gitlab-org/gitlab-runner/issues/3144
        # forces us to output something every couple of seconds :(
        print("Still alive: {}".format(datetime.datetime.now().time()))
        self.restart_timer()

    def check_arch(self, architecture):
        url = makeurl(self.apiurl, [
                      'build', self.project, self.repository, architecture], {'view': 'status'})
        root = ET.parse(http_GET(url)).getroot()
        return root.get('code') == 'finished'

    def check_all_archs(self):
        all_done = True
        for arch in self.archs:
            if not self.check_arch(arch):
                # don't exit early, we want the OBS checks
                all_done = False
        if all_done:
            print("Repo is finished")
        return all_done

    def start_consuming(self):
        self.restart_timer()
        super(Listener, self).start_consuming()

    def on_message(self, unused_channel, method, properties, body):
        try:
            body = json.loads(body)
        except ValueError:
            return
        if body['project'] != self.project:
            return
        if method.routing_key.find('.obs.package.') > 0:
            if body['repository'] != self.repository:
                return
            self.restart_timer()
            if method.routing_key.endswith('.obs.package.build_fail'):
                print("Failed build: {}/{}/{}/{}".format(
                    body['project'], body['repository'], body['arch'], body['package']))
            elif method.routing_key.endswith('.obs.package.build_success'):
                print("Succeed build: {}/{}/{}/{}".format(
                    body['project'], body['repository'], body['arch'], body['package']))
            elif method.routing_key.endswith('.obs.package.build_unchanged'):
                print("Unchanged build: {}/{}/{}/{}".format(
                    body['project'], body['repository'], body['arch'], body['package']))
        elif method.routing_key.endswith('.obs.repo.build_finished'):
            if body['repo'] != self.repository:
                return
            self.restart_timer()
            print(
                "Repo finished: {}/{}/{}".format(body['project'], body['repo'], body['arch']))
            if self.check_all_archs():
                self.stop()
        else:
            self.logger.warning(
                "unknown rabbitmq message {}".format(method.routing_key))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Bot to sync openQA status to OBS')
    parser.add_argument("--apiurl", '-A', type=str, help='API URL of OBS')
    parser.add_argument('-p', '--project', type=str, help='Project to check')
    parser.add_argument('-r', '--repository', type=str,
                        help='Repository to check')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']

    if apiurl.endswith('suse.de'):
        amqp_prefix = 'suse'
    else:
        amqp_prefix = 'opensuse'

    logging.basicConfig(level=logging.WARN)

    listener = Listener(apiurl, amqp_prefix, args.project, args.repository)

    try:
        listener.run()
        listener.check_failures()
    except KeyboardInterrupt:
        listener.stop()
