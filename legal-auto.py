#!/usr/bin/python

# Copyright (c) 2017 SUSE LLC
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
#
# SPDX-License-Identifier: MIT

from pprint import pprint
import os
import sys
import re
import logging
from optparse import OptionParser
import cmdln
import requests as REQ
import json
import time
import urllib2

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

try:
    import cPickle as pickle
except:
    import pickle

import osc.conf
import osc.core
import ReviewBot

from osclib.comments import CommentAPI

http_GET = osc.core.http_GET


class LegalAuto(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        self.legaldb = None
        self.legaldb_headers = {}
        self.apinick = None
        self.message = None
        if self.ibs:
            self.apinick = 'ibs#'
        else:
            self.apinick = 'obs#'
        self.override_allow = False  # Handled via external tool.
        self.request_default_return = True

    def retried_GET(self, url):
        try:
            return http_GET(url)
        except urllib2.HTTPError, e:
            if 500 <= e.code <= 599:
                print 'Retrying {}'.format(url)
                time.sleep(1)
                return self.retried_GET(url)
            raise e

    def request_priority(self):
        prio = self.request.priority or 'moderate'
        prios = {'low': 1, 'moderate': 2, 'important': 3, 'critical': 4}
        prio = prios.get(prio, 4) * 2
        if self.ibs:
            prio = prio + 1
        return prio

    def request_nick(self, id=None):
        if not id:
            id = self.request.reqid
        return self.apinick + id

    def create_db_entry(self, src_project, src_package, src_rev):
        params = {'api': self.apiurl, 'project': src_project, 'package': src_package,
                  'external_link': self.request_nick(),
                  'created': self.request.statehistory[0].when + ' UTC'}
        if src_rev:
            params['rev'] = src_rev
        url = osc.core.makeurl(self.legaldb, ['packages'], params)

        package = REQ.post(url, headers=self.legaldb_headers).json()
        if not 'saved' in package:
            return None
        package = package['saved']
        url = osc.core.makeurl(self.legaldb, ['requests'], {'external_link': self.request_nick(),
                                                            'package': package['id']})
        request = REQ.post(url, headers=self.legaldb_headers).json()
        return [package['id']]

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s" % (src_project,
                                                src_package, src_rev, target_project, target_package))
        to_review = self.open_reviews.get(self.request_nick(), None)
        if to_review:
            self.logger.info("Found " + json.dumps(to_review))
        to_review = to_review or self.create_db_entry(
            src_project, src_package, src_rev)
        if not to_review:
            return None
        for pack in to_review:
            url = osc.core.makeurl(self.legaldb, ['package', str(pack)])
            report = REQ.get(url, headers=self.legaldb_headers).json()
            if report.get('priority', 0) != self.request_priority():
                print "Update priority %d" % self.request_priority()
                url = osc.core.makeurl(
                    self.legaldb, ['package', str(pack)], {'priority': self.request_priority()})
                REQ.patch(url, headers=self.legaldb_headers)
            state = report.get('state', 'BROKEN')
            if state == 'obsolete':
                url = osc.core.makeurl(self.legaldb, ['packages', 'import', str(pack)], {
                                       'result': 'reopened in obs', 'state': 'new'})
                package = REQ.post(url, headers=self.legaldb_headers).json()
                # reopen
                return None
            if not state in ['acceptable', 'correct', 'unacceptable']:
                return None
            if state == 'unacceptable':
                user = report.get('reviewing_user', None)
                if not user:
                    self.message = 'declined'
                    print("unacceptable without user %d" % report.get('id'))
                    return None
                comment = report.get('result', None)
                if comment:
                    self.message = "@{} declined the legal report with the following comment: {}".format(
                        user, comment)
                else:
                    self.message = "@{} declined the legal report".format(user)
                    return None
                return False
            # print url, json.dumps(report)
        self.message = 'ok'
        return True

    def check_one_request(self, req):
        self.message = None
        result = super(LegalAuto, self).check_one_request(req)
        if result is None and self.message is not None:
            print(self.message, req.reqid)
        return result

    def check_action__default(self, req, a):
        self.logger.error("unhandled request type %s" % a.type)
        return True

    def prepare_review(self):
        url = osc.core.makeurl(self.legaldb, ['requests'])
        req = REQ.get(url, headers=self.legaldb_headers)
        req = req.json()
        self.open_reviews = {}
        requests = []
        for hash in req['requests']:
            ext_link = str(hash['external_link'])
            self.open_reviews[ext_link] = list(set(hash['packages']))
            if ext_link.startswith(self.apinick):
                rq = ext_link[len(self.apinick):]
                requests.append('@id=' + rq)
        while len(requests):
            batch = requests[:200]
            requests = requests[200:]
            match = "(state/@name='declined' or state/@name='revoked' or state/@name='superseded')"
            match += ' and (' + ' or '.join(sorted(batch)) + ')'
            url = osc.core.makeurl(
                self.apiurl, ['search', 'request', 'id'], {'match': match})
            # prefer POST because of the length
            root = ET.parse(osc.core.http_POST(url)).getroot()
            for request in root.findall('request'):
                self.delete_from_db(request.get('id'))

    def delete_from_db(self, id):
        url = osc.core.makeurl(
            self.legaldb, ['requests'], {'external_link': self.request_nick(id)})
        REQ.delete(url, headers=self.legaldb_headers)

    # overload as we need to get of the bot_request
    def _set_review(self, req, state):
        if self.dryrun:
            self.logger.debug("dry setting %s to %s with %s" %
                              (req.reqid, state, self.message))
            return

        self.logger.debug("setting %s to %s" % (req.reqid, state))
        osc.core.change_review_state(apiurl=self.apiurl,
                                     reqid=req.reqid, newstate=state,
                                     by_group=self.review_group,
                                     by_user=self.review_user, message=self.message)
        self.delete_from_db(req.reqid)

    def _pkl_path(self):
        CACHE_DIR = os.path.expanduser('~/.cache/osc-plugin-factory')
        return os.path.join(CACHE_DIR, 'legaldb')

    def update_project(self, project):
        try:
            with open(self._pkl_path(), 'rb') as pkl_file:
                self.pkg_cache = pickle.load(pkl_file)
        except (IOError, EOFError):
            self.pkg_cache = {}

        self.packages = []
        # we can't create packages for requests - we need a nonewpackages=1 first
        # self._query_requests(project)
        self._query_sources(project)
        self._save_pkl()
        url = osc.core.makeurl(self.legaldb, ['products', project])
        request = REQ.patch(url, headers=self.legaldb_headers, data={'id': self.packages}).json()

    def _save_pkl(self):
        pkl_file = open(self._pkl_path(), 'wb')
        pickle.dump(self.pkg_cache, pkl_file)
        pkl_file.close()

    def _query_sources(self, project):
        url = osc.core.makeurl(
            self.apiurl, ['source', project], {'view': 'info'})
        f = self.retried_GET(url)
        root = ET.parse(f).getroot()
        #root = ET.fromstring(open('prj.info').read())
        for si in root.findall('sourceinfo'):
            if si.findall('error'):
                continue
            package = si.get('package')
            if ':' in package:
                continue
            if package == 'patchinfo' or package.startswith('patchinfo.'):
                continue
            # handle maintenance links - we only want the latest
            match = re.match(r'(\S+)\.\d+$', package)
            if match:
                if si.find('filename').text == match.group(1) + '.spec':
                    continue
            match = re.match(r'(\S+)\.imported_\d+$', package)
            if match:
                continue
            skip = False
            for l in si.findall('linked'):
                lpackage = l.get('package')
                # strip sle11's .imported_ suffix
                lpackage = re.sub(r'\.imported_\d+$', '', lpackage)
                # check if the lpackage is origpackage.NUMBER
                match = re.match(r'(\S+)\.\d+$', lpackage)
                if match and match.group(1) == package:
                    lpackage = package
                if package != lpackage:
                    print "SKIP", package, "it links to", lpackage
                    skip = True
                    break
            if skip:
                continue
            self.packages.append(self._add_source(project, project, package, si.get('rev')))

    def _query_requests(self, project):
        match = "(state/@name='new' or state/@name='review') and (action/target/@project='{}')"
        match = match.format(project)
        url = osc.core.makeurl(
            self.apiurl, ['search', 'request'], {'match': match})
        f = osc.core.http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            for a in rq.findall('action'):
                if not a.attrib['type'] in ('submit', 'maintenance_incident'):
                    continue
                source = a.find('source')
                revision = source.attrib.get('rev')
                src_project = source.attrib['project']
                package = source.attrib['package']
                self._add_source(project, src_project, package, revision)

    def _add_source(self, tproject, sproject, package, revision):
        params = {'api': self.apiurl, 'project': sproject, 'package': package,
                  'external_link': tproject}
        if revision:
            params['rev'] = revision
        hkey = hash(json.dumps(params, sort_keys=True))
        if hkey in self.pkg_cache:
            return self.pkg_cache[hkey]

        params['priority'] = 1
        url = osc.core.makeurl(self.legaldb, ['packages'], params)

        try:
            obj = REQ.post(url, headers=self.legaldb_headers).json()
        except:
            return None
        if not 'saved' in obj:
            return None
        print "PKG", tproject, sproject, package, revision, obj['saved']['id']
        self.pkg_cache[hkey] = obj['saved']['id']
        return self.pkg_cache[hkey]


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = LegalAuto

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option("--legaldb", dest='legaldb', metavar='URL',
                          default='http://legaldb.suse.de', help="Use different legaldb deployment")
        parser.add_option("--token", dest='token', metavar='STRING',
                          default=False, help="Use token to authenticate")
        return parser

    def do_project(self, subcmd, opts, *projects):
        """${cmd_name}: Overloaded to create/update product
        """
        for project in projects:
            self.checker.update_project(project)

    def setup_checker(self):
        if not self.options.user and not self.options.group:
            self.options.group = 'legal-auto'
        bot = ReviewBot.CommandLineInterface.setup_checker(self)
        bot.legaldb = self.options.legaldb
        if self.options.token:
            bot.legaldb_headers['Authorization'] = 'Token ' + self.options.token
        return bot


if __name__ == "__main__":
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.WARNING)
    requests_log.propagate = False

    app = CommandLineInterface()
    sys.exit(app.main())
