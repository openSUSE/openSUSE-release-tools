#!/usr/bin/python3

# SPDX-License-Identifier: MIT

import os
import os.path
import sys
import re
import logging
from optparse import OptionParser
import cmdln
from dateutil.parser import parse
from datetime import timezone, timedelta
import requests as REQ
import json
import time
import yaml

from urllib.error import HTTPError
from lxml import etree as ET

import osc.conf
import osc.core
from osclib.cache_manager import CacheManager
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
        except HTTPError as e:
            if 500 <= e.code <= 599:
                self.logger.debug('Retrying {}'.format(url))
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
        if 'saved' not in package:
            return None
        package = package['saved']
        url = osc.core.makeurl(self.legaldb, ['requests'], {'external_link': self.request_nick(),
                                                            'package': package['id']})
        REQ.post(url, headers=self.legaldb_headers)
        return [package['id']]

    def valid_for_opensuse(self, target_project, report):
        if target_project != "openSUSE:Factory":
            return False
        indexed = report.get('indexed', None)
        if not indexed:
            return False
        datetime = parse(indexed)
        # give the legaldb 2 hours to find a match (so we prefer acceptable/correct over preliminary)
        if datetime.now(timezone.utc) - datetime < timedelta(hours=2):
            return False
        return True

    def default_good(self, _, package):
        if package == 'patchinfo' or package.startswith('patchinfo.'):
            return True
        if package.endswith('.SUSE_Channels'):
            return True
        return False

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s" % (src_project,
                                                src_package, src_rev, target_project, target_package))
        if self.default_good(src_project, src_package):
            return True
        to_review = self.open_reviews.get(self.request_nick(), None)
        if to_review:
            self.logger.info("Found {}".format(json.dumps(to_review)))
        to_review = to_review or self.create_db_entry(
            src_project, src_package, src_rev)
        if not to_review:
            return None
        self.message = None
        for pack in to_review:
            url = osc.core.makeurl(self.legaldb, ['package', str(pack)])
            report = REQ.get(url, headers=self.legaldb_headers).json()
            if report.get('priority', 0) != self.request_priority():
                self.logger.debug('Update priority {}'.format(self.request_priority()))
                url = osc.core.makeurl(
                    self.legaldb, ['package', str(pack)], {'priority': self.request_priority()})
                REQ.patch(url, headers=self.legaldb_headers)
            state = report.get('state', 'BROKEN')
            if state == 'obsolete':
                url = osc.core.makeurl(self.legaldb, ['packages', 'import', str(pack)], {
                                       'result': 'reopened in obs', 'state': 'new'})
                REQ.post(url, headers=self.legaldb_headers)
                # reopen
                return None
            if state == 'new' and self.valid_for_opensuse(target_project, report):
                self.message = 'The legal review is accepted preliminary. The package may require actions later on.'
                # reduce legal review priority - we're no longer waiting
                url = osc.core.makeurl(
                    self.legaldb, ['package', str(pack)], {'priority': 1})
                if not self.dryrun:
                    REQ.patch(url, headers=self.legaldb_headers)
                continue
            if state not in ['acceptable', 'correct', 'unacceptable']:
                return None
            if state == 'unacceptable':
                user = report.get('reviewing_user', None)
                if not user:
                    self.message = 'declined'
                    self.logger.warning("unacceptable without user %d" % report.get('id'))
                    return None
                comment = report.get('result', None).encode('utf-8')
                if comment:
                    self.message = "@{} declined the legal report with the following comment: {}".format(
                        user, comment)
                else:
                    self.message = "@{} declined the legal report".format(user)
                    return None
                return False
            # print url, json.dumps(report)
        if not self.message:
            self.message = 'ok'
        return True

    def check_one_request(self, req):
        self.message = None
        result = super(LegalAuto, self).check_one_request(req)
        if result is None and self.message is not None:
            self.logger.debug("Result of {}: {}".format(req.reqid, self.message))
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

    def update_project(self, project):
        yaml_path = os.path.join(CacheManager.directory('legal-auto'), '{}.yaml'.format(project))
        try:
            with open(yaml_path, 'r') as file:
                self.pkg_cache = yaml.load(file, Loader=yaml.SafeLoader)
        except (IOError, EOFError):
            self.pkg_cache = {}

        self.packages = []
        self._query_sources_for_product_import(project)
        with open(yaml_path, 'w') as file:
            yaml.dump(self.pkg_cache, file)
        url = osc.core.makeurl(self.legaldb, ['products', project])
        REQ.patch(url, headers=self.legaldb_headers, data={'id': self.packages})

    def _query_sources_for_product_import(self, project):
        url = osc.core.makeurl(
            self.apiurl, ['source', project], {'view': 'info'})
        f = self.retried_GET(url)
        root = ET.parse(f).getroot()
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
            for link in si.findall('linked'):
                lpackage = link.get('package')
                # strip sle11's .imported_ suffix
                lpackage = re.sub(r'\.imported_\d+$', '', lpackage)
                # check if the lpackage is origpackage.NUMBER
                match = re.match(r'(\S+)\.\d+$', lpackage)
                if match and match.group(1) == package:
                    lpackage = package
                if package != lpackage:
                    self.logger.info("SKIP {}, it links to {}".format(package, lpackage))
                    skip = True
                    break
            if skip:
                continue
            self.packages.append(self._add_source(project, project, package, si.get('rev')))

    def _add_source(self, tproject, sproject, package, revision):
        params = {'api': self.apiurl, 'project': sproject, 'package': package,
                  'external_link': tproject}
        if revision:
            params['rev'] = revision
        old_id = self.pkg_cache.get(package, { None: None }).get(revision, None)
        if old_id:
            return old_id

        params['priority'] = 1
        url = osc.core.makeurl(self.legaldb, ['packages'], params)

        try:
            obj = REQ.post(url, headers=self.legaldb_headers).json()
        except HTTPError:
            return None
        if 'saved' not in obj:
            return None
        legaldb_id = obj['saved']['id']
        self.logger.debug("PKG {}/{}[{}]->{} is {}".format(sproject, package, revision, tproject, legaldb_id))
        self.pkg_cache[package] = { revision: legaldb_id }
        if obj['saved']['state'] == 'obsolete':
            url = osc.core.makeurl(self.legaldb, ['packages', 'import', str(legaldb_id)], {
                                   'result': f'Reopened for {tproject}', 'state': 'new',
                                   'external_link': tproject, 'priority': 1})
            package = REQ.post(url, headers=self.legaldb_headers).json()

        return obj['saved']['id']


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = LegalAuto

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option("--legaldb", dest='legaldb', metavar='URL',
                          default='http://legaldb.suse.de', help="Use different legaldb deployment")
        return parser

    def do_project(self, subcmd, opts, *projects):
        """${cmd_name}: Overloaded to create/update product

        ${cmd_usage}
        ${cmd_option_list}
        """
        for project in projects:
            self.checker.update_project(project)

    def setup_checker(self):
        if not self.options.user and not self.options.group:
            self.options.group = 'legal-auto'
        bot = ReviewBot.CommandLineInterface.setup_checker(self)
        bot.legaldb = self.options.legaldb
        bot.legaldb_headers['Authorization'] = 'Token ' + osc.conf.config['legaldb_token']
        return bot


if __name__ == "__main__":
    requests_log = logging.getLogger("requests.packages.urllib3")
    requests_log.setLevel(logging.WARNING)
    requests_log.propagate = False
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").propagate = False

    app = CommandLineInterface()
    sys.exit(app.main())
