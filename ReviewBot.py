#!/usr/bin/python

from pprint import pprint
import os, sys, re
import logging
from optparse import OptionParser
import cmdln
from collections import namedtuple
from collections import OrderedDict
from osclib.cache import Cache
from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.core import devel_project_fallback
from osclib.core import group_members
from osclib.core import maintainers_get
from osclib.memoize import memoize
from osclib.memoize import memoize_session_reset
from osclib.stagingapi import StagingAPI
import signal
import datetime
import time
import yaml

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

from osc import conf
import osc.core
import urllib2
from itertools import count

class PackageLookup(object):
    """ helper class to manage 00Meta/lookup.yml
    """

    def __init__(self, apiurl = None):
        self.apiurl = apiurl
        # dict[project][package]
        self.lookup = {}

    def get(self, project, package):
        if not project in self.lookup:
            self.load(project)

        return self.lookup[project].get(package, None)

    def reset(self):
        self.lookup = {}

    def load(self, project):
        fh = self._load_lookup_file(project)
        self.lookup[project] = yaml.safe_load(fh) if fh else {}

    def _load_lookup_file(self, prj):
        try:
            return osc.core.http_GET(osc.core.makeurl(self.apiurl,
                                ['source', prj, '00Meta', 'lookup.yml']))
        except urllib2.HTTPError as e:
            # in case the project doesn't exist yet (like sle update)
            if e.code != 404:
                raise e
            return None


class ReviewBot(object):
    """
    A generic obs request reviewer
    Inherit from this class and implement check functions for each action type:

    def check_action_<type>(self, req, action):
        return (None|True|False)
    """

    DEFAULT_REVIEW_MESSAGES = { 'accepted' : 'ok', 'declined': 'review failed' }
    REVIEW_CHOICES = ('normal', 'no', 'accept', 'accept-onpass', 'fallback-onfail', 'fallback-always')

    COMMENT_MARKER_REGEX = re.compile(r'<!-- (?P<bot>[^ ]+) state=(?P<state>[^ ]+)(?: result=(?P<result>[^ ]+))? -->')

    # map of default config entries
    config_defaults = {
            # list of tuples (prefix, apiurl, submitrequestprefix)
            # set this if the obs instance maps another instance into it's
            # namespace
            'project_namespace_api_map' : [
                ('openSUSE.org:', 'https://api.opensuse.org', 'obsrq'),
                ],
            }

    def __init__(self, apiurl = None, dryrun = False, logger = None, user = None, group = None):
        self.apiurl = apiurl
        self.ibs = apiurl.startswith('https://api.suse.de')
        self.dryrun = dryrun
        self.logger = logger
        self.review_user = user
        self.review_group = group
        self.requests = []
        self.review_messages = ReviewBot.DEFAULT_REVIEW_MESSAGES
        self._review_mode = 'normal'
        self.fallback_user = None
        self.fallback_group = None
        self.comment_api = CommentAPI(self.apiurl)
        self.bot_name = self.__class__.__name__
        self.only_one_action = False
        self.request_default_return = None
        self.comment_handler = False
        self.override_allow = True
        self.override_group_key = '{}-override-group'.format(self.bot_name.lower())
        self.lookup = PackageLookup(self.apiurl)

        self.load_config()

    def _load_config(self, handle = None):
        d = self.__class__.config_defaults
        y = yaml.safe_load(handle) if handle is not None else {}
        return namedtuple('BotConfig', sorted(d.keys()))(*[ y.get(p, d[p]) for p in sorted(d.keys()) ])

    def load_config(self, filename = None):
        if filename:
            with open(filename, 'r') as fh:
                self.config = self._load_config(fh)
        else:
            self.config = self._load_config()

    def staging_api(self, project):
        if project not in self.staging_apis:
            Config.get(self.apiurl, project)
            self.staging_apis[project] = StagingAPI(self.apiurl, project)

        return self.staging_apis[project]

    @property
    def review_mode(self):
        return self._review_mode

    @review_mode.setter
    def review_mode(self, value):
        if value not in self.REVIEW_CHOICES:
            raise Exception("invalid review option: %s"%value)
        self._review_mode = value

    def set_request_ids(self, ids):
        for rqid in ids:
            u = osc.core.makeurl(self.apiurl, [ 'request', rqid ], { 'withfullhistory' : '1' })
            r = osc.core.http_GET(u)
            root = ET.parse(r).getroot()
            req = osc.core.Request()
            req.read(root)
            self.requests.append(req)

    # function called before requests are reviewed
    def prepare_review(self):
        pass

    def check_requests(self):
        self.staging_apis = {}

        # give implementations a chance to do something before single requests
        self.prepare_review()
        for req in self.requests:
            self.logger.info("checking %s"%req.reqid)
            self.request = req

            override = self.request_override_check(req)
            if override is not None:
                good = override
            else:
                good = self.check_one_request(req)

            if self.review_mode == 'no':
                good = None
            elif self.review_mode == 'accept':
                good = True

            if good is None:
                self.logger.info("%s ignored"%req.reqid)
            elif good:
                self._set_review(req, 'accepted')
            elif self.review_mode != 'accept-onpass':
                self._set_review(req, 'declined')

    @memoize(session=True)
    def request_override_check_users(self, project):
        """Determine users allowed to override review in a comment command."""
        config = Config.get(self.apiurl, project)

        users = []
        group = config.get('staging-group')
        if group:
            users += group_members(self.apiurl, group)

        if self.override_group_key:
            override_group = config.get(self.override_group_key)
            if override_group:
                users += group_members(self.apiurl, override_group)

        return users

    def request_override_check(self, request):
        """Check for a comment command requesting review override."""
        if not self.override_allow:
            return None

        comments = self.comment_api.get_comments(request_id=request.reqid)
        users = self.request_override_check_users(request.actions[0].tgt_project)
        for args, who in self.comment_api.command_find(
            comments, self.review_user, 'override', users):
            message = 'overridden by {}'.format(who)
            override = args[1] or None
            if override == 'accept':
                self.review_messages['accepted'] = message
                return True

            if override == 'decline':
                self.review_messages['declined'] = message
                return False

    def _set_review(self, req, state):
        doit = self.can_accept_review(req.reqid)
        if doit is None:
            self.logger.info("can't change state, %s does not have the reviewer"%(req.reqid))

        newstate = state

        by_user = self.fallback_user
        by_group = self.fallback_group

        msg = self.review_messages[state] if state in self.review_messages else state
        self.logger.info("%s %s: %s"%(req.reqid, state, msg))

        if state == 'declined':
            if self.review_mode == 'fallback-onfail':
                self.logger.info("%s needs fallback reviewer"%req.reqid)
                self.add_review(req, by_group=by_group, by_user=by_user, msg="Automated review failed. Needs fallback reviewer.")
                newstate = 'accepted'
        elif self.review_mode == 'fallback-always':
            self.add_review(req, by_group=by_group, by_user=by_user, msg='Adding fallback reviewer')

        if doit == True:
            self.logger.debug("setting %s to %s"%(req.reqid, state))
            if not self.dryrun:
                osc.core.change_review_state(apiurl = self.apiurl,
                        reqid = req.reqid, newstate = newstate,
                        by_group=self.review_group,
                        by_user=self.review_user, message=msg)
        else:
            self.logger.debug("%s review not changed"%(req.reqid))

    # allow_duplicate=True should only be used if it makes sense to force a
    # re-review in a scenario where the bot adding the review will rerun.
    # Normally a declined review will automatically be reopened along with the
    # request and any other bot reviews already added will not be touched unless
    # the issuing bot is rerun which does not fit normal workflow.
    def add_review(self, req, by_group=None, by_user=None, by_project=None, by_package=None,
                   msg=None, allow_duplicate=False):
        query = {
            'cmd': 'addreview'
        }
        if by_group:
            query['by_group'] = by_group
        elif by_user:
            query['by_user'] = by_user
        elif by_project:
            query['by_project'] = by_project
            if by_package:
                query['by_package'] = by_package
        else:
            raise osc.oscerr.WrongArgs("missing by_*")

        for r in req.reviews:
            if (r.by_group == by_group and
                r.by_project == by_project and
                r.by_package == by_package and
                r.by_user == by_user and
                # Only duplicate when allow_duplicate and state != new.
                (not allow_duplicate or r.state == 'new')):
                del query['cmd']
                self.logger.debug('skipped adding duplicate review for {}'.format(
                    '/'.join(query.values())))
                return

        u = osc.core.makeurl(self.apiurl, ['request', req.reqid], query)
        if self.dryrun:
            self.logger.info('POST %s' % u)
            return

        r = osc.core.http_POST(u, data=msg)
        code = ET.parse(r).getroot().attrib['code']
        if code != 'ok':
            raise Exception('non-ok return code: {}'.format(code))

    def devel_project_review_add(self, request, project, package, message='adding devel project review'):
        devel_project, devel_package = devel_project_fallback(self.apiurl, project, package)
        if not devel_project:
            self.logger.warning('no devel project found for {}/{}'.format(project, package))
            return False

        self.add_review(request, by_project=devel_project, by_package=devel_package, msg=message)
        return True

    def devel_project_review_ensure(self, request, project, package, message='submitter not devel maintainer'):
        if not self.devel_project_review_needed(request, project, package):
            self.logger.debug('devel project review not needed')
            return True

        return self.devel_project_review_add(request, project, package, message)

    def devel_project_review_needed(self, request, project, package):
        author = request.get_creator()
        maintainers = set(maintainers_get(self.apiurl, project, package))

        if author in maintainers:
            return False

        # Carried over from maintbot, but seems haphazard.
        for review in request.reviews:
            if review.by_user in maintainers:
                return False

        return True

    def check_one_request(self, req):
        """
        check all actions in one request.

        calls helper functions for each action type

        return None if nothing to do, True to accept, False to reject
        """

        # Copy original values to revert changes made to them.
        self.review_messages = self.DEFAULT_REVIEW_MESSAGES.copy()

        if self.only_one_action and len(req.actions) != 1:
            self.review_messages['declined'] = 'Only one action per request supported'
            return False

        if self.comment_handler is not False:
            self.comment_handler_add()

        overall = True
        for a in req.actions:
            # Store in-case sub-classes need direct access to original values.
            self.action = a

            fn = 'check_action_%s'%a.type
            if not hasattr(self, fn):
                fn = 'check_action__default'
            func = getattr(self, fn)
            ret = func(req, a)

            # In the case of multiple actions take the "lowest" result where the
            # order from lowest to highest is: False, None, True.
            if overall is not False:
                if ((overall is True and ret is not True) or
                    (overall is None and ret is False)):
                    overall = ret

        return overall

    @staticmethod
    def _is_patchinfo(pkgname):
        return pkgname == 'patchinfo' or pkgname.startswith('patchinfo.')

    def check_action_maintenance_incident(self, req, a):
        if self._is_patchinfo(a.src_package):
            self.logger.debug('ignoring patchinfo action')
            return None

        # Duplicate src_package as tgt_package since prior to assignment to a
        # specific incident project there is no target package (odd API). After
        # assignment it is still assumed the target will match the source. Since
        # the ultimate goal is the tgt_releaseproject the incident is treated
        # similar to staging in that the intermediate result is not the final
        # and thus the true target project (ex. openSUSE:Maintenance) is not
        # used for check_source_submission().
        tgt_package = a.src_package
        if a.tgt_releaseproject is not None:
            suffix = '.' + a.tgt_releaseproject.replace(':', '_')
            if tgt_package.endswith(suffix):
                tgt_package = tgt_package[:-len(suffix)]

        # Note tgt_releaseproject (product) instead of tgt_project (maintenance).
        return self.check_source_submission(a.src_project, a.src_package, a.src_rev,
                                            a.tgt_releaseproject, tgt_package)

    def check_action_maintenance_release(self, req, a):
        pkgname = a.src_package
        if self._is_patchinfo(pkgname):
            self.logger.debug('ignoring patchinfo action')
            return None
        linkpkg = self._get_linktarget_self(a.src_project, pkgname)
        if linkpkg is not None:
            pkgname = linkpkg
        # packages in maintenance have links to the target. Use that
        # to find the real package name
        (linkprj, linkpkg) = self._get_linktarget(a.src_project, pkgname)
        if linkpkg is None or linkprj is None or linkprj != a.tgt_project:
            self.logger.error("%s/%s is not a link to %s"%(a.src_project, pkgname, a.tgt_project))
            return False
        else:
            pkgname = linkpkg
        return self.check_source_submission(a.src_project, a.src_package, None, a.tgt_project, pkgname)

    def check_action_submit(self, req, a):
        return self.check_source_submission(a.src_project, a.src_package, a.src_rev, a.tgt_project, a.tgt_package)

    def check_action__default(self, req, a):
        # Disable any comment handler to avoid making a comment even if
        # comment_write() is called by another bot wrapping __default().
        self.comment_handler_remove()

        message = 'unhandled request type {}'.format(a.type)
        self.logger.error(message)
        self.review_messages['accepted'] += ': ' + message
        return self.request_default_return

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        """ default implemention does nothing """
        self.logger.info("%s/%s@%s -> %s/%s"%(src_project, src_package, src_rev, target_project, target_package))
        return None

    @staticmethod
    @memoize(session=True)
    def _get_sourceinfo(apiurl, project, package, rev=None):
        query = { 'view': 'info' }
        if rev is not None:
            query['rev'] = rev
        url = osc.core.makeurl(apiurl, ('source', project, package), query=query)
        try:
            return ET.parse(osc.core.http_GET(url)).getroot()
        except (urllib2.HTTPError, urllib2.URLError):
            return None

    def get_originproject(self, project, package, rev=None):
        root = ReviewBot._get_sourceinfo(self.apiurl, project, package, rev)
        if root is None:
            return None

        originproject = root.find('originproject')
        if originproject is not None:
            return originproject.text

        return None

    def get_sourceinfo(self, project, package, rev=None):
        root = ReviewBot._get_sourceinfo(self.apiurl, project, package, rev)
        if root is None:
            return None

        props = ('package', 'rev', 'vrev', 'srcmd5', 'lsrcmd5', 'verifymd5')
        return namedtuple('SourceInfo', props)(*[ root.get(p) for p in props ])

    # TODO: what if there is more than _link?
    def _get_linktarget_self(self, src_project, src_package):
        """ if it's a link to a package in the same project return the name of the package"""
        prj, pkg = self._get_linktarget(src_project, src_package)
        if prj is None or prj == src_project:
            return pkg

    def _get_linktarget(self, src_project, src_package):

        query = {}
        url = osc.core.makeurl(self.apiurl, ('source', src_project, src_package), query=query)
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError:
            return (None, None)

        if root is not None:
            linkinfo = root.find("linkinfo")
            if linkinfo is not None:
                return (linkinfo.get('project'), linkinfo.get('package'))

        return (None, None)

    def _has_open_review_by(self, root, by_what, reviewer):
        states = set([review.get('state') for review in root.findall('review') if review.get(by_what) == reviewer])
        if not states:
            return None
        elif 'new' in states:
            return True
        return False

    def can_accept_review(self, request_id):
        """return True if there is a new review for the specified reviewer"""
        states = set()
        url = osc.core.makeurl(self.apiurl, ('request', str(request_id)))
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
            if self.review_user and self._has_open_review_by(root, 'by_user', self.review_user):
                return True
            if self.review_group and self._has_open_review_by(root, 'by_group', self.review_group):
                return True
        except urllib2.HTTPError as e:
            print('ERROR in URL %s [%s]' % (url, e))
        return False

    def set_request_ids_search_review(self):
        review = None
        if self.review_user:
            review = "@by_user='%s' and @state='new'" % self.review_user
        if self.review_group:
            review = osc.core.xpath_join(review, "@by_group='%s' and @state='new'" % self.review_group)
        url = osc.core.makeurl(self.apiurl, ('search', 'request'), { 'match': "state/@name='review' and review[%s]" % review, 'withfullhistory': 1 } )
        root = ET.parse(osc.core.http_GET(url)).getroot()

        self.requests = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            self.requests.append(req)

    # also used by openqabot
    def ids_project(self, project, typename):
        url = osc.core.makeurl(self.apiurl, ('search', 'request'),
                               { 'match': "(state/@name='review' or state/@name='new') and (action/target/@project='%s' and action/@type='%s')" % (project, typename),
                                 'withfullhistory': 1 })
        root = ET.parse(osc.core.http_GET(url)).getroot()

        ret = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            ret.append(req)
        return ret

    def set_request_ids_project(self, project, typename):
        self.requests = self.ids_project(project, typename)

    def comment_handler_add(self, level=logging.INFO):
        """Add handler to start recording log messages for comment."""
        self.comment_handler = CommentFromLogHandler(level)
        self.logger.addHandler(self.comment_handler)

    def comment_handler_remove(self):
        self.logger.removeHandler(self.comment_handler)

    def comment_handler_lines_deduplicate(self):
        self.comment_handler.lines = list(OrderedDict.fromkeys(self.comment_handler.lines))

    def comment_write(self, state='done', result=None, project=None, package=None,
                      request=None, message=None, identical=False, only_replace=False,
                      info_extra=None, info_extra_identical=True, bot_name_suffix=None):
        """Write comment if not similar to previous comment and replace old one.

        The state, result, and info_extra (dict) are combined to create the info
        that is passed to CommentAPI methods for creating a marker and finding
        previous comments. self.bot_name, which defaults to class, will be used
        as the primary matching key. When info_extra_identical is set to False
        info_extra will not be included when finding previous comments to
        compare message against.

        A comment from the same bot will be replaced when a new comment is
        written. The only_replace flag will restrict to only writing a comment
        if a prior one is being replaced. This can be useful for writing a final
        comment that indicates a change from previous uncompleted state, but
        only makes sense to post if a prior comment was posted.

        The project, package, and request variables control where the comment is
        placed. If no value is given the default is the request being reviewed.

        If no message is provided the content will be extracted from
        self.comment_handler.line which is provided by CommentFromLogHandler. To
        use this call comment_handler_add() at the point which messages should
        start being collected. Alternatively the self.comment_handler setting
        may be set to True to automatically set one on each request.

        The previous comment body line count is compared to see if too similar
        to bother posting another comment which is useful for avoiding
        re-posting comments that contain irrelevant minor changes. To force an
        exact match use the identical flag to replace any non-identical
        comment body.
        """
        if project:
            kwargs = {'project_name': project}
            if package:
                kwargs['package_name'] = package
        else:
            if request is None:
                request = self.request
            kwargs = {'request_id': request.reqid}
        debug_key = '/'.join(kwargs.values())

        if message is None:
            if not len(self.comment_handler.lines):
                self.logger.debug('skipping empty comment for {}'.format(debug_key))
                return
            message = '\n\n'.join(self.comment_handler.lines)

        bot_name = self.bot_name
        if bot_name_suffix:
            bot_name = '::'.join([bot_name, bot_name_suffix])

        info = {'state': state, 'result': result}
        if info_extra and info_extra_identical:
            info.update(info_extra)

        comments = self.comment_api.get_comments(**kwargs)
        comment, _ = self.comment_api.comment_find(comments, bot_name, info)

        if info_extra and not info_extra_identical:
            # Add info_extra once comment has already been matched.
            info.update(info_extra)

        message = self.comment_api.add_marker(message, bot_name, info)
        message = self.comment_api.truncate(message.strip())

        if (comment is not None and
            ((identical and
              # Remove marker from comments since handled during comment_find().
              self.comment_api.remove_marker(comment['comment']) ==
              self.comment_api.remove_marker(message)) or
             (not identical and comment['comment'].count('\n') == message.count('\n')))
        ):
            # Assume same state/result and number of lines in message is duplicate.
            self.logger.debug('previous comment too similar on {}'.format(debug_key))
            return

        if comment is None:
            self.logger.debug('broadening search to include any state on {}'.format(debug_key))
            comment, _ = self.comment_api.comment_find(comments, bot_name)
        if comment is not None:
            self.logger.debug('removing previous comment on {}'.format(debug_key))
            if not self.dryrun:
                self.comment_api.delete(comment['id'])
        elif only_replace:
            self.logger.debug('no previous comment to replace on {}'.format(debug_key))
            return

        self.logger.debug('adding comment to {}: {}'.format(debug_key, message))
        if not self.dryrun:
            self.comment_api.add_comment(comment=message, **kwargs)

        self.comment_handler_remove()

    def _check_matching_srcmd5(self, project, package, rev, history_limit = 5):
        """check if factory sources contain the package and revision. check head and history"""
        self.logger.debug("checking %s in %s"%(package, project))
        try:
            si = osc.core.show_package_meta(self.apiurl, project, package)
        except (urllib2.HTTPError, urllib2.URLError):
            si = None
        if si is None:
            self.logger.debug("new package")
            return None
        else:
            si = self.get_sourceinfo(project, package)
            if rev == si.verifymd5:
                self.logger.debug("srcmd5 matches")
                return True

        if history_limit:
            self.logger.debug("%s not the latest version, checking history", rev)
            u = osc.core.makeurl(self.apiurl, [ 'source', project, package, '_history' ], { 'limit': history_limit })
            try:
                r = osc.core.http_GET(u)
            except urllib2.HTTPError as e:
                self.logger.debug("package has no history!?")
                return None

            root = ET.parse(r).getroot()
            # we need this complicated construct as obs doesn't honor
            # the 'limit' parameter use above for obs interconnect:
            # https://github.com/openSUSE/open-build-service/issues/2545
            for revision, i in zip(reversed(root.findall('revision')), count()):
                node = revision.find('srcmd5')
                if node is None:
                    continue
                self.logger.debug("checking %s"%node.text)
                if node.text == rev:
                    self.logger.debug("got it, rev %s"%revision.get('rev'))
                    return True
                if i == history_limit:
                    break

            self.logger.debug("srcmd5 not found in history either")

        return False


class CommentFromLogHandler(logging.Handler):
    def __init__(self, level=logging.INFO):
        super(CommentFromLogHandler, self).__init__(level)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


class CommandLineInterface(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)
        Cache.init()
        self.clazz = ReviewBot

    def get_optparser(self):
        parser = cmdln.Cmdln.get_optparser(self)
        parser.add_option("--apiurl", '-A', metavar="URL", help="api url")
        parser.add_option("--user",  metavar="USER", help="reviewer user name")
        parser.add_option("--group",  metavar="GROUP", help="reviewer group name")
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--osc-debug", action="store_true", help="osc debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        parser.add_option("--review-mode", dest='review_mode', choices=ReviewBot.REVIEW_CHOICES, help="review behavior")
        parser.add_option("--fallback-user", dest='fallback_user', metavar='USER', help="fallback review user")
        parser.add_option("--fallback-group", dest='fallback_group', metavar='GROUP', help="fallback review group")
        parser.add_option('-c', '--config', dest='config', metavar='FILE', help='read config file FILE')

        return parser

    def postoptparse(self):
        level = None
        if (self.options.debug):
            level = logging.DEBUG
        elif (self.options.verbose):
            level = logging.INFO

        logging.basicConfig(level=level, format='[%(levelname).1s] %(message)s')
        self.logger = logging.getLogger(self.optparser.prog)

        conf.get_config(override_apiurl = self.options.apiurl)

        if (self.options.osc_debug):
            conf.config['debug'] = 1

        self.checker = self.setup_checker()
        if self.options.config:
            self.checker.load_config(self.options.config)

        if self.options.review_mode:
            self.checker.review_mode = self.options.review_mode

        if self.options.fallback_user:
            self.checker.fallback_user = self.options.fallback_user

        if self.options.fallback_group:
            self.checker.fallback_group = self.options.fallback_group

    def setup_checker(self):
        """ reimplement this """
        apiurl = conf.config['apiurl']
        if apiurl is None:
            raise osc.oscerr.ConfigError("missing apiurl")
        user = self.options.user
        group = self.options.group
        # if no args are given, use the current oscrc "owner"
        if user is None and group is None:
            user = conf.get_apiurl_usr(apiurl)

        return self.clazz(apiurl = apiurl, \
                dryrun = self.options.dry, \
                user = user, \
                group = group, \
                logger = self.logger)

    def do_id(self, subcmd, opts, *args):
        """${cmd_name}: check the specified request ids

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.checker.set_request_ids(args)
        self.checker.check_requests()

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    def do_review(self, subcmd, opts, *args):
        """${cmd_name}: check requests that have the specified user or group as reviewer

        ${cmd_usage}
        ${cmd_option_list}
        """
        if self.checker.review_user is None and self.checker.review_group is None:
            raise osc.oscerr.WrongArgs("missing reviewer (user or group)")

        def work():
            self.checker.set_request_ids_search_review()
            self.checker.check_requests()

        self.runner(work, opts.interval)

    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
    def do_project(self, subcmd, opts, project, typename):
        """${cmd_name}: check all requests of specified type to specified

        ${cmd_usage}
        ${cmd_option_list}
        """

        def work():
            self.checker.set_request_ids_project(project, typename)
            self.checker.check_requests()

        self.runner(work, opts.interval)

    def runner(self, workfunc, interval):
        """ runs the specified callback every <interval> minutes or
        once if interval is None or 0
        """
        class ExTimeout(Exception):
            """raised on timeout"""

        if interval:
            def alarm_called(nr, frame):
                raise ExTimeout()
            signal.signal(signal.SIGALRM, alarm_called)

        while True:
            try:
                workfunc()
            except Exception as e:
                self.logger.exception(e)

            if interval:
                if os.isatty(0):
                    self.logger.info("sleeping %d minutes. Press enter to check now ..."%interval)
                    signal.alarm(interval*60)
                    try:
                        raw_input()
                    except ExTimeout:
                        pass
                    signal.alarm(0)
                    self.logger.info("recheck at %s"%datetime.datetime.now().isoformat())
                else:
                    self.logger.info("sleeping %d minutes." % interval)
                    time.sleep(interval * 60)

                # Reset all memoize session caches which are designed for single
                # tool run and not extended usage.
                memoize_session_reset()

                # Reload checker to flush instance variables and thus any config
                # or caches they may contain.
                self.postoptparse()

                continue

            break

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )
