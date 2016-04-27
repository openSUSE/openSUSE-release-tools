#!/usr/bin/python
# Copyright (c) 2015,2016 SUSE LLC
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

from optparse import OptionParser
from pprint import pformat, pprint
import cmdln
import logging
import os
import re
import sys
import time
from simplejson import JSONDecodeError
from collections import namedtuple
try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.conf
import osc.core

from osclib.comments import CommentAPI

import ReviewBot

from openqa_client.client import OpenQA_Client
from openqa_client import exceptions as openqa_exceptions

Package = namedtuple('Package', ('name', 'version', 'release'))

pkgname_re = re.compile(r'(?P<name>.+)-(?P<version>[^-]+)-(?P<release>[^-]+)\.(?P<arch>[^.]+)\.rpm')

# QA Results
QA_UNKNOWN = 0
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3

comment_marker_re = re.compile(r'<!-- openqa state=(?P<state>done|seen)(?: result=(?P<result>accepted|declined))? -->')

logger = None

# old stuff, for reference
#    def filterchannel(self, apiurl, prj, packages):
#        """ filter list of package objects to only include those actually released into prj"""
#
#        prefix = 'SUSE:Updates:'
#        logger.debug(prj)
#        if not prj.startswith(prefix):
#            return packages
#
#        channel = prj[len(prefix):].replace(':', '_')
#
#        url = osc.core.makeurl(apiurl, ('source',  'SUSE:Channels', channel, '_channel'))
#        root = ET.parse(osc.core.http_GET(url)).getroot()
#
#        package_names = set([p.name for p in packages])
#        in_channel = set([p.attrib['name'] for p in root.iter('binary') if p.attrib['name'] in package_names])
#
#        return [p for p in packages if p.name in in_channel]


class Update(object):

    def __init__(self, settings):
        self._settings = settings
        self._settings['_NOOBSOLETEBUILD'] = '1'

    def settings(self, src_prj, dst_prj, packages, req=None):
        return self._settings.copy()


class openSUSEUpdate(Update):

    def __init__(self, settings):
        Update.__init__(self, settings)

    def settings(self, src_prj, dst_prj, packages, req=None):
        settings = Update.settings(self, src_prj, dst_prj, packages, req)

        settings['BUILD'] = src_prj
        if req:
            settings['BUILD'] += ':' + req.reqid

        # openSUSE:Maintenance key
        settings['IMPORT_GPG_KEYS'] = 'gpg-pubkey-b3fd7e48-5549fd0f'

        settings['ZYPPER_ADD_REPO_PREFIX'] = 'incident'

        if packages:
            # XXX: this may fail in various ways
            # - conflicts between subpackages
            # - added packages
            # - conflicts with installed packages (e.g sendmail vs postfix)
            settings['INSTALL_PACKAGES'] = ' '.join(set([p.name for p in packages]))
            settings['VERIFY_PACKAGE_VERSIONS'] = ' '.join(['{} {}-{}'.format(p.name, p.version, p.release) for p in packages])

        settings['ZYPPER_ADD_REPOS'] = 'http://download.opensuse.org/repositories/%s/%s/' % (src_prj.replace(':', ':/'), dst_prj.replace(':', '_'))
        settings['ADDONURL'] = settings['ZYPPER_ADD_REPOS']

        settings['ISO'] = 'openSUSE-Leap-42.1-DVD-x86_64.iso'

        settings['WITH_MAIN_REPO'] = 1
        settings['WITH_UPDATE_REPO'] = 1

        return settings


class TestUpdate(openSUSEUpdate):

    def __init__(self, settings):
        openSUSEUpdate.__init__(self, settings)

    def settings(self, src_prj, dst_prj, packages, req=None):
        settings = openSUSEUpdate.settings(self, src_prj, dst_prj, packages, req)

        settings['IMPORT_GPG_KEYS'] = 'testkey'

        return settings


PROJECT_OPENQA_SETTINGS = {
    'openSUSE:13.2:Update': [
        openSUSEUpdate(
            {
                'DISTRI': 'opensuse',
                'VERSION': '13.2',
                'FLAVOR': 'Maintenance',
                'ARCH': 'x86_64',
            }),
        openSUSEUpdate(
            {
                'DISTRI': 'opensuse',
                'VERSION': '13.2',
                'FLAVOR': 'Maintenance',
                'ARCH': 'i586',
            }),
    ],
    'openSUSE:Leap:42.1:Update': [
        openSUSEUpdate(
            {
                'DISTRI': 'opensuse',
                'VERSION': '42.1',
                'FLAVOR': 'Maintenance',
                'ARCH': 'x86_64',
            }),
    ],
}


class OpenQABot(ReviewBot.ReviewBot):
    """ check ABI of library packages
    """

    def __init__(self, *args, **kwargs):
        self.force = False
        self.openqa = None
        self.do_comments = True
        if 'force' in kwargs:
            if kwargs['force'] is True:
                self.force = True
            del kwargs['force']
        if 'openqa' in kwargs:
            self.openqa = OpenQA_Client(server=kwargs['openqa'])
            del kwargs['openqa']
        if 'do_comments' in kwargs:
            if kwargs['do_comments'] is not None:
                self.do_comments = kwargs['do_comments']
            del kwargs['do_comments']

        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        self.logger.debug(self.do_comments)

        self.commentapi = CommentAPI(self.apiurl)

    def check_action_maintenance_release(self, req, a):
        # we only look at the binaries of the patchinfo
        if a.src_package != 'patchinfo':
            return None

        if a.tgt_project not in PROJECT_OPENQA_SETTINGS:
            self.logger.warn("not handling %s" % a.tgt_project)
            return None

        packages = []
        # patchinfo collects the binaries and is build for an
        # unpredictable architecture so we need iterate over all
        url = osc.core.makeurl(
            self.apiurl,
            ('build', a.src_project, a.tgt_project.replace(':', '_')))
        root = ET.parse(osc.core.http_GET(url)).getroot()
        for arch in [n.attrib['name'] for n in root.findall('entry')]:
            query = {'nosource': 1}
            url = osc.core.makeurl(
                self.apiurl,
                ('build', a.src_project, a.tgt_project.replace(':', '_'), arch, a.src_package),
                query=query)

            root = ET.parse(osc.core.http_GET(url)).getroot()

            for binary in root.findall('binary'):
                m = pkgname_re.match(binary.attrib['filename'])
                if m:
                    # can't use arch here as the patchinfo mixes all
                    # archs
                    packages.append(Package(m.group('name'), m.group('version'), m.group('release')))

        if not packages:
            raise Exception("no packages found")

        self.logger.debug('found packages %s', ' '.join(set([p.name for p in packages])))

        for update in PROJECT_OPENQA_SETTINGS[a.tgt_project]:
            settings = update.settings(a.src_project, a.tgt_project, packages, req)
            if settings is not None:
                self.logger.info("posting %s %s %s", settings['VERSION'], settings['ARCH'], settings['BUILD'])
                self.logger.debug('\n'.join(["  %s=%s" % i for i in settings.items()]))
                if not self.dryrun:
                    try:
                        ret = self.openqa.openqa_request('POST', 'isos', data=settings, retries=1)
                        self.logger.info(pformat(ret))
                    except JSONDecodeError, e:
                        self.logger.error(e)
                        # TODO: record error
                    except openqa_exceptions.RequestError, e:
                        self.logger.error(e)

        return None

    def check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package):

        ReviewBot.ReviewBot.check_source_submission(self, src_project, src_package, src_rev, dst_project, dst_package)

    def request_get_openqa_jobs(self, req):
        ret = None
        types = set([a.type for a in req.actions])
        if 'maintenance_release' in types:
            if len(types) != 1:
                raise Exception("can't handle types mixed with maintenance_release")
            src_prjs = set([a.src_project for a in req.actions])
            if len(src_prjs) != 1:
                raise Exception("can't handle maintenance_release from different incidents")
            build = src_prjs.pop() + ':' + req.reqid
            tgt_prjs = set([a.tgt_project for a in req.actions])
            ret = []
            for prj in tgt_prjs:
                if prj in PROJECT_OPENQA_SETTINGS:
                    for u in PROJECT_OPENQA_SETTINGS[prj]:
                        s = u.settings(build, prj, [])
                        ret += self.openqa.openqa_request(
                            'GET', 'jobs',
                            {
                                'distri': s['DISTRI'],
                                'version': s['VERSION'],
                                'arch': s['ARCH'],  # FIXME: no supported by API
                                'flavor': s['FLAVOR'],
                                'build': s['BUILD'],
                                'scope': 'relevant',
                            })['jobs']

        return ret

    def calculate_qa_status(self, jobs=None):
        if not jobs:
            return QA_UNKNOWN

        j = dict()
        has_failed = False
        in_progress = False
        for job in jobs:
            if job['clone_id']:
                continue
            name = job['name']
            if name in j and int(job['id']) < int(j[name]['id']):
                continue
            j[name] = job
            self.logger.debug('job %s in openQA: %s %s %s %s', job['id'], job['settings']['VERSION'], job['settings']['TEST'], job['state'], job['result'])
            if job['state'] not in ('cancelled', 'done'):
                in_progress = True
            else:
                if job['result'] != 'passed':
                    has_failed = True

        if not j:
            return QA_UNKNOWN
        if in_progress:
            return QA_INPROGRESS
        if has_failed:
            return QA_FAILED

        return QA_PASSED

    def check_publish_enabled(self, project):
        url = osc.core.makeurl(self.apiurl, ('source', project, '_meta'))
        root = ET.parse(osc.core.http_GET(url)).getroot()
        node = root.find('publish')
        if node is not None and node.find('disable') is not None:
            return False
        return True

    def add_comment(self, req, msg, state, result=None):
        if not self.do_comments:
            return

        (comment_id, comment_state, comment_result) = self.find_obs_request_comment(req, state)
        if comment_id is not None:
            self.logger.debug("found comment %s, state %s", comment_id, comment_state)
            return

        comment = "<!-- openqa state=%s%s -->\n" % (state, ' result=%s' % result if result else '')
        comment += "\n" + msg

        self.logger.debug("adding comment to %s, state %s result %s", req.reqid, state, result)
        self.logger.debug("message: %s", msg)
        if not self.dryrun:
            self.commentapi.add_comment(request_id=req.reqid, comment=str(comment))

    def openqa_overview_url_from_settings(self, settings):
        return osc.core.makeurl(self.openqa.baseurl, ['tests'], {'match': settings['BUILD']})
#        return osc.core.makeurl( self.openqa.baseurl, ['tests', 'overview'], {
#                'distri': settings['DISTRI'],
#                'version': settings['VERSION'],
#                'build': settings['BUILD'],
#            }) #.replace('&', "%26")

    def find_failed_modules(self, job):
        failed = []
        for module in job['modules']:
            if module['result'] != 'failed':
                continue
            failed.append(module['name'])
        return failed

    def check_one_request(self, req):
        ret = None

        # just patch apiurl in to avoid having to pass it around
        req.apiurl = self.apiurl
        try:
            jobs = self.request_get_openqa_jobs(req)
            qa_state = self.calculate_qa_status(jobs)
            self.logger.debug("request %s state %s", req.reqid, qa_state)
            msg = None
            if self.force or qa_state == QA_UNKNOWN:
                ret = ReviewBot.ReviewBot.check_one_request(self, req)
                jobs = self.request_get_openqa_jobs(req)

                if self.force:
                    # make sure to delete previous comments if we're forcing
                    (comment_id, comment_state, comment_result) = self.find_obs_request_comment(req)
                    if comment_id is not None:
                        self.logger.debug("deleting old comment %s", comment_id)
                        if not self.dryrun:
                            self.commentapi.delete(comment_id)

                if not jobs:
                    msg = "no openQA tests defined"
                    self.add_comment(req, msg, 'done', 'accepted')
                    ret = True
                else:
                    url = self.openqa_overview_url_from_settings(jobs[0]['settings'])
                    self.logger.debug("url %s", url)
                    msg = "now testing in [openQA](%s)" % url
                    self.add_comment(req, msg, 'seen')
            elif qa_state == QA_FAILED or qa_state == QA_PASSED:
                url = self.openqa_overview_url_from_settings(jobs[0]['settings'])
                if qa_state == QA_PASSED:
                    self.logger.debug("request %s passed", req.reqid)
                    msg = "openQA test [passed](%s)" % url
                    state = 'accepted'
                    ret = True
                else:
                    self.logger.debug("request %s failed", req.reqid)
                    msg = "openQA test *[FAILED](%s)*\n" % url
                    state = 'declined'
                    ret = False
                for job in jobs:
                    modules = self.find_failed_modules(job)
                    if modules != []:
                        msg += '\n- [%s](%s) failed %s in %s' % (
                            job['id'],
                            osc.core.makeurl(self.openqa.baseurl, ['tests', str(job['id'])]),
                            job['settings']['TEST'], ','.join(modules))
                self.add_comment(req, msg, 'done', state)
            elif qa_state == QA_INPROGRESS:
                self.logger.debug("request %s still in progress", req.reqid)
            else:
                raise Exception("unknown QA state %d", qa_state)

        except Exception, e:
            import traceback
            self.logger.error("unhandled exception in openQA Bot")
            self.logger.error(traceback.format_exc())
            ret = None

        return ret

    def find_obs_request_comment(self, req, state=None):
        """Return previous comments (should be one)."""
        if self.do_comments:
            comments = self.commentapi.get_comments(request_id=req.reqid)
            for c in comments.values():
                m = comment_marker_re.match(c['comment'])
                if m and (state is None or state == m.group('state')):
                    return c['id'], m.group('state'), m.group('result')
        return None, None, None


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)
        parser.add_option("--force", action="store_true", help="recheck requests that are already considered done")
        parser.add_option("--no-comment", dest='comment', action="store_false", help="don't actually post comments to obs")
        parser.add_option("--openqa", metavar='HOST', help="openqa api host")
        return parser

    def setup_checker(self):

        apiurl = osc.conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        if user is None:
            user = osc.conf.get_apiurl_usr(apiurl)

        if not self.options.openqa:
            raise osc.oscerr.ConfigError("missing openqa url")

        global logger
        logger = self.logger

        return OpenQABot(
            apiurl=apiurl,
            dryrun=self.options.dry,
            user=user,
            do_comments=self.options.comment,
            openqa=self.options.openqa,
            force=self.options.force,
            logger=self.logger)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
