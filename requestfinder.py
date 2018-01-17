#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2018 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from ConfigParser import ConfigParser
from xdg.BaseDirectory import load_first_config
from xml.etree import cElementTree as ET
import sys
import cmdln
import logging
import urllib2
import osc.core
import yaml
import os

import ToolBase

makeurl = osc.core.makeurl

logger = logging.getLogger()


class Requestfinder(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.devel = None

    def fill_package_meta(self, project):
        self.package_metas = dict()
        url = makeurl(self.apiurl, ['search', 'package'], "match=[@project='%s']" % project)
        root = ET.fromstring(self.cached_GET(url))
        for p in root.findall('package'):
            name = p.attrib['name']
            self.package_metas[name] = p

    def find_requests(self, xquery):

        if self.devel:
            self.fill_package_meta('openSUSE:Factory')

        url = osc.core.makeurl(self.apiurl, ('search', 'request'), {"match": xquery})
        root = ET.parse(osc.core.http_GET(url)).getroot()

        self.requests = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            if self.devel:
                p = req.actions[0].tgt_package
                pm = self.package_metas[p] if p in self.package_metas else None
                devel = pm.find('devel') if pm else None
                if devel is None or devel.get('project') == self.devel:
                    self.requests.append(req)
            else:
                self.requests.append(req)

        return self.requests


class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

        self.cp = ConfigParser()
        d = load_first_config('opensuse-release-tools')
        if d:
            self.cp.read(os.path.join(d, 'requestfinder.conf'))

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('--devel', dest='devel', metavar='PROJECT',
                          help='only packages with devel project')
        return parser

    def setup_tool(self):
        tool = Requestfinder()
        tool.devel = self.options.devel
        return tool

    def _load_settings(self, settings, name):
        section = 'settings {}'.format(name)
        for option in settings.keys():
            if self.cp.has_option(section, option):
                settings[option] = self.cp.get(section, option).replace('\n', ' ')

    @cmdln.option('--exclude-project', metavar='PROJECT', action='append', help='exclude review by specific project')
    @cmdln.option('--exclude-user', metavar='USER', action='append', help='exclude review by specific user')
    @cmdln.option('--query', metavar='filterstr', help='filter string')
    @cmdln.option('--action', metavar='action', help='action (accept/decline)')
    @cmdln.option('--settings', metavar='settings', help='settings to load from config file')
    @cmdln.option('-m', '--message', metavar="message", help="message")
    def do_review(self, subcmd, opts):
        """${cmd_name}: print commands for reviews

        ${cmd_usage}
        ${cmd_option_list}
        """

        settings = {
            'action': 'accept',
            'message': 'ok',
            'query': None,
            'exclude-project': None,
            'exclude-user': None,
        }

        if opts.settings:
            self._load_settings(settings, opts.settings)

        if opts.action:
            settings['action'] = opts.action
            settings['message'] = opts.action

        if opts.message:
            settings['message'] = opts.message

        if opts.query:
            settings['query'] = opts.query

        if not settings['query']:
            raise Exception('please specify query')

        rqs = self.tool.find_requests(settings['query'])
        for r in rqs:
            if r.actions[0].type == 'submit':
                print(' '.join(('#', r.reqid, r.actions[0].type, r.actions[0].src_project, r.actions[0].src_package, r.actions[0].tgt_project)))
            else:
                print(' '. join(('#', r.reqid, r.actions[0].type, r.actions[0].tgt_project)))
            for review in r.reviews:
                if review.state != 'new':
                    continue

                if review.by_project:
                    skip = False
                    if settings['exclude-project']:
                        for p in settings['exclude-project']:
                            if review.by_project.startswith(p):
                                skip = True
                                break
                    if not skip:
                        if review.by_package:
                            print("osc review %s -m '%s' -P %s -p %s %s" % (settings['action'], settings['message'], review.by_project, review.by_package, r.reqid))
                        else:
                            print("osc review %s -m '%s' -P %s %s" % (settings['action'], settings['message'], review.by_project, r.reqid))
                elif review.by_group:
                    print("osc review %s -m '%s' -G %s %s" % (settings['action'], settings['message'], review.by_group, r.reqid))
                elif review.by_user:
                    skip = False
                    if settings['exclude-user']:
                        for u in settings['exclude-user']:
                            if review.by_user == u:
                                skip = True
                                break
                    if not skip:
                        print("osc review %s -m '%s' -U %s %s" % (settings['action'], settings['message'], review.by_user, r.reqid))

    @cmdln.option('--query', metavar='filterstr', help='filter string')
    @cmdln.option('--action', metavar='action', help='action (accept/decline)')
    @cmdln.option('--settings', metavar='settings', help='settings to load from config file')
    @cmdln.option('-m', '--message', metavar="message", help="message")
    def do_request(self, subcmd, opts):
        """${cmd_name}: print commands for requests

        ${cmd_usage}
        ${cmd_option_list}
        """

        settings = {
            'action': 'reopen',
            'message': 'reopen',
            'query': None,
        }

        if opts.settings:
            self._load_settings(settings, opts.settings)

        rqs = self.tool.find_requests(settings['query'])
        for r in rqs:
            print('#', r.reqid, r.get_creator(), r.actions[0].src_project, r.actions[0].src_package, r.actions[0].tgt_project)
            print("osc rq {} -m '{}' {}".format(settings['action'], settings['message'], r.reqid))

    def help_examples(self):
        return """$ cat > ~/.config/opensuse-release-tools/requestfinder.conf << EOF
        [settings foo]
        query = (review[@by_project='example' and @state='new']
                 and state/@name='review'
                 and action/source/@project='openSUSE:Factory'
                 and action/target/@project='openSUSE:Leap:15.0'
        exclude-user = repo-checker
        exclude-project = openSUSE:Leap:15.0:Staging
        message = override
        action = accept
        EOF
        $ ${name} review --settings foo | tee doit.sh
        ./doit.sh
        """

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
