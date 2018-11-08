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
from lxml import etree as ET
from collections import namedtuple

import sys
import cmdln
import logging
import urllib2
import osc.core
import yaml
import os
import ldap

import ToolBase

logger = logging.getLogger()

FACTORY = "openSUSE:Factory"

Owner = namedtuple('Owner', ('kind', 'name'))
Person = namedtuple('Person', ('login', 'email', 'realname'))

class BugownerTool(ToolBase.ToolBase):

    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.project = None
        self.reference_projects = None
        self.package_metas = dict()
        self.release_managers = None
        self.persons = {}

    def resolve_person(self, name):
        if name in self.persons:
            return self.persons[name]

        url = self.makeurl(['person', name])
        root = ET.fromstring(self.cached_GET(url))

        person = Person(*[ root.find('./{}'.format(field)).text for field in Person._fields ])
        self.persons[name] = person

        return person

    def find_packages_with_missing_bugowner(self):
        url = self.makeurl(['search', 'missing_owner'], { 'project': self.project, 'filter': 'bugowner'})
        root = ET.fromstring(self.cached_GET(url))

        missing = []
        for node in root.findall('missing_owner'):
            missing.append(node.get('package'))

        return missing

    def find_owner(self, package, role = 'bugowner'):
        # XXX: not actually looking for package but binary
        # https://github.com/openSUSE/open-build-service/issues/4359
        url = self.makeurl(['search', 'owner'], { 'binary': package})
        root = ET.fromstring(self.cached_GET(url))
        ret = []
        for node in root.findall('./owner/person[@role="{}"]'.format(role)):
            ret.append(Owner('person', node.get('name')))
        for node in root.findall('./owner/group[@role="{}"]'.format(role)):
            ret.append(Owner('group', node.get('name')))

        return ret

    def add_bugowner(self, package, owner):
        url = self.makeurl(['source', self.project, package, '_meta'])
        root = ET.fromstring(self.cached_GET(url))
        idname = 'userid' if owner.kind == 'person' else 'groupid'
        # XXX: can't use 'and' here to filter for bugowner too
        exists = root.findall('./{}[@{}="{}"]'.format(owner.kind, idname, owner.name))
        for node in exists:
            if node.get('role') == 'bugowner':
                logger.debug("%s/%s already has %s %s", self.project, package, owner.kind, owner.name)
            return

        node = ET.SubElement(root, owner.kind)
        node.set(idname, owner.name)
        node.set('role', 'bugowner')

        data = ET.tostring(root)
        logger.debug(data)
        self.http_PUT(url, data=data)

    def package_get_last_committer(self, package):
        project = self.project
        srcrev = osc.core.get_source_rev(self.apiurl, project, package)

        if 'requestid' in srcrev:
            r = osc.core.get_request(self.apiurl, srcrev['requestid'])
            user = r.statehistory[0].who
        else:
            user = srcrev['user']

        if self.is_release_manager(user):
            logging.debug("%s was last touched by %s, ignored."%(package, user))
            return None

        return [ Owner('person', user) ]

    def is_release_manager(self, name):
        if self.release_managers is None:
            self.release_managers = set()
            url = self.makeurl(['group', 'sle-release-managers'])
            root = ET.fromstring(self.cached_GET(url))
            for node in root.findall('.//person[@userid]'):
                self.release_managers.add(node.get('userid'))
            # XXX: hardcoded bot
            self.release_managers.add('leaper')
            logger.debug("release managers %s", self.release_managers)

        return name in self.release_managers


class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-p', '--project', dest='project', metavar='PROJECT',
                        help='project to process (default: %s)' % FACTORY,
                        default = FACTORY)
        parser.add_option('--reference-project', metavar='PROJECT',
                action='append', help='reference project')
        return parser

    def setup_tool(self):
        tool = BugownerTool()
        tool.project = self.options.project
        return tool

    def do_missing(self, subcmd, opts):
        """${cmd_name}: find packages with missing bugowner

        Beware of https://github.com/openSUSE/open-build-service/issues/4172
        when using this with SLE service packs or update projects

        ${cmd_usage}
        ${cmd_option_list}
        """

        pkgs = self.tool.find_packages_with_missing_bugowner()
        for p in pkgs:
            print(p)

    @cmdln.option('-r', '--role', metavar='ROLE', help='role to look up', default="bugowner")
    @cmdln.option('-s', '--set', action='store_true', help='set bugowner in specified project')
    @cmdln.option('--request', action='store_true', help='print osc request lines')
    @cmdln.option('--employee', action='store_true', help='only filter employees')
    def do_owner(self, subcmd, opts, *package):
        """${cmd_name}: find owners of the given pacakge

        ${cmd_usage}
        ${cmd_option_list}
        """

        l = ldap.initialize("ldap://pan.suse.de")
        l.simple_bind_s()

        for p in package:
            owners = self.tool.find_owner(p, opts.role)
            if not owners:
                logger.info("%s does not have owners", p)
                continue
            for o in owners:
                logger.info("%s -> %s %s", p, o.kind, o.name)
                if opts.set:
                    self.tool.add_bugowner(p, o)
                elif opts.request:
                    name = o.name
                    if o.kind == 'group':
                        name = 'group:' + name
                    print("osc -A {} reqbs -r bugowner -m 'copy bug owner from previous codestream' {} {} {}".format(self.tool.apiurl, self.tool.project, p, name))
                elif opts.employee:
                    if o.kind != 'person':
                        logger.debug('%s not a person', o.name)
                        continue
                    person = self.tool.resolve_person(o.name)
                    if person.email.endswith('@suse.com'):
                        print p, o.name
                    else:
                        logger.debug('%s skipped', o.name)

    def do_addbugowner(self, subcmd, opts, package, *persons):
        """${cmd_name}: add person as bugowner unless already set

        ${cmd_usage}
        ${cmd_option_list}
        """

        for p in persons:
            o = Owner('person', p)
            logger.info("%s -> %s %s", package, o.kind, o.name)
            self.tool.add_bugowner(p, o)

    @cmdln.option('--set', action='store_true',
                  help='request bugowner')
    @cmdln.option('--request', action='store_true', help='print osc request lines')
    def do_lastsubmitter(self, subcmd, opts, *packages):
        """${cmd_name}: show last committer for packages

        excludes release managers

        ${cmd_name} PROJECT PACKAGE...

        ${cmd_option_list}
        """

        for p in packages:
            owners = self.tool.package_get_last_committer(p)
            if not owners:
                logger.info("%s does not have owners", p)
                continue
            for o in owners:
                logger.info("%s -> %s %s", p, o.kind, o.name)
                if opts.set:
                    self.tool.add_bugowner(p, o)
                if opts.request:
                    name = o.name
                    if o.kind == 'group':
                        name = 'group:' + name
                    print("osc -A {} reqbs -r bugowner -m 'add last submitter as bug owner' {} {} {}".format(self.tool.apiurl, self.tool.project, p, name))

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
