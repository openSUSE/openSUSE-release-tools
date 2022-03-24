#!/usr/bin/python3

import glob
import os
import re
import shutil
import subprocess
import sys

from lxml import etree as ET

import osc.conf
import osc.core
from osc.util.helper import decode_list
from osclib.conf import Config
from osclib.core import devel_project_get
from osclib.core import devel_project_fallback
from osclib.core import group_members
from osclib.core import package_kind
from osclib.core import create_add_role_request
from osc.core import show_project_meta
from osc.core import get_request_list
from urllib.error import HTTPError

import ReviewBot
from osclib.conf import str2bool


class CheckSource(ReviewBot.ReviewBot):

    SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.request_default_return = True

        self.skip_add_reviews = False

    def target_project_config(self, project):
        # Load project config and allow for remote entries.
        config = Config.get(self.apiurl, project)

        self.single_action_require = str2bool(config.get('check-source-single-action-require', 'False'))
        self.ignore_devel = not str2bool(config.get('devel-project-enforce', 'False'))
        self.in_air_rename_allow = str2bool(config.get('check-source-in-air-rename-allow', 'False'))
        self.add_review_team = str2bool(config.get('check-source-add-review-team', 'True'))
        self.review_team = config.get('review-team')
        self.mail_release_list = config.get('mail-release-list')
        self.staging_group = config.get('staging-group')
        self.required_maintainer = config.get('required-source-maintainer', '')
        self.devel_whitelist = config.get('devel-whitelist', '').split()
        self.skip_add_reviews = False

        if self.action.type == 'maintenance_incident':
            # The workflow effectively enforces the names to match and the
            # parent code sets target_package from source_package so this check
            # becomes useless and awkward to perform.
            self.in_air_rename_allow = True

            # The target project will be set to product and thus inherit
            # settings, but override since real target is not product.
            self.single_action_require = False

            # It might make sense to supersede maintbot, but for now.
            self.skip_add_reviews = True

    def is_good_name(self, package, target_package):
        self.logger.debug(f"is_good_name {package} <-> {target_package}")
        if target_package is None:
            # if the name doesn't matter, existance is all
            return package is not None

        return target_package == package

    def package_source_parse(self, project, package, revision=None, target_package=None):
        ret = self._package_source_parse(project, package, revision)

        if self.is_good_name(ret['name'], target_package):
            return ret

        d = {}
        for repo in osc.core.get_repositories_of_project(self.apiurl, project):
            r = self._package_source_parse(project, package, revision, repo)
            if r['name'] is not None:
                d[r['name']] = r

        if len(d) == 1:
            # here is only one so use that
            ret = d[next(iter(d))]
        else:
            # check if any name matches
            self.logger.debug("found multiple names %s", ', '.join(d.keys()))
            for n, r in d.items():
                if n == target_package:
                    ret = r
                    break

            if not self.is_good_name(ret['name'], target_package):
                self.logger.error("none of the names matched")

        return ret

    def check_source_submission(self, source_project, source_package, source_revision, target_project, target_package):
        super(CheckSource, self).check_source_submission(source_project,
                                                         source_package, source_revision, target_project, target_package)
        self.target_project_config(target_project)

        if self.single_action_require and len(self.request.actions) != 1:
            self.review_messages['declined'] = 'Only one action per request allowed'
            return False

        if source_revision is None:
            self.review_messages['declined'] = 'Submission not from a pinned source revision'
            return False

        kind = package_kind(self.apiurl, target_project, target_package)
        if kind == 'meta':
            self.review_messages['accepted'] = 'Skipping all checks for meta packages'
            return True
        elif (kind is not None and kind != 'source'):
            self.review_messages['declined'] = 'May not modify a non-source package of type {}'.format(kind)
            return False

        inair_renamed = target_package != source_package

        if not self.ignore_devel:
            self.logger.info('checking if target package exists and has devel project')
            devel_project, devel_package = devel_project_get(self.apiurl, target_project, target_package)
            if devel_project:
                if (source_project != devel_project or source_package != devel_package) and \
                   not(source_project == target_project and source_package == target_package):
                    # Not from proper devel project/package and not self-submission.
                    self.review_messages['declined'] = 'Expected submission from devel package %s/%s' % (
                        devel_project, devel_package)
                    return False
            else:
                # Check to see if other packages exist with the same source project
                # which indicates that the project has already been used as devel.
                if not self.is_devel_project(source_project, target_project):
                    self.review_messages['declined'] = (
                        '%s is not a devel project of %s, submit the package to a devel project first. '
                        'See https://en.opensuse.org/openSUSE:How_to_contribute_to_Factory#How_to_request_a_new_devel_project for details.'
                    ) % (source_project, target_project)
                    return False
        else:
            if source_project.endswith(':Update'):
                # Allow for submission like:
                # - source: openSUSE:Leap:15.0:Update/google-compute-engine.8258
                # - target: openSUSE:Leap:15.1/google-compute-engine
                # Note: home:jberry:Update would also be allowed via this condition,
                # but that should be handled by leaper and human review.
                # Ignore a dot in package name (ex. tpm2.0-abrmd) and instead
                # only look for ending in dot number.
                match = re.match(r'(.*)\.\d+$', source_package)
                if match:
                    inair_renamed = target_package != match.group(1)

        if not self.source_has_correct_maintainers(source_project):
            declined_msg = (
                'This request cannot be accepted unless %s is a maintainer of %s.' %
                (self.required_maintainer, source_project)
            )

            req = self.__ensure_add_role_request(source_project)
            if req:
                declined_msg += ' Created the add_role request %s for addressing this problem.' % req

            self.review_messages['declined'] = declined_msg
            return False

        if not self.in_air_rename_allow and inair_renamed:
            self.review_messages['declined'] = 'Source and target package names must match'
            return False

        # Checkout and see if renaming package screws up version parsing.
        dir = os.path.expanduser('~/co/%s' % self.request.reqid)
        if os.path.exists(dir):
            self.logger.warning('directory %s already exists' % dir)
            shutil.rmtree(dir)
        os.makedirs(dir)
        os.chdir(dir)

        old_info = {'version': None}
        try:
            CheckSource.checkout_package(self.apiurl, target_project, target_package, pathname=dir,
                                         server_service_files=True, expand_link=True)
            shutil.rmtree(os.path.join(target_package, '.osc'))
            os.rename(target_package, '_old')
            old_info = self.package_source_parse(target_project, target_package)
        except HTTPError as e:
            if e.code == 404:
                self.logger.info('target package does not exist %s/%s' % (target_project, target_package))
            else:
                raise e

        CheckSource.checkout_package(self.apiurl, source_project, source_package, revision=source_revision,
                                     pathname=dir, server_service_files=True, expand_link=True)
        os.rename(source_package, target_package)
        shutil.rmtree(os.path.join(target_package, '.osc'))

        new_info = self.package_source_parse(source_project, source_package, source_revision, target_package)
        filename = new_info.get('filename', '')
        if not (filename.endswith('.kiwi') or filename == 'Dockerfile') and new_info['name'] != target_package:
            shutil.rmtree(dir)
            self.review_messages['declined'] = "A package submitted as %s has to build as 'Name: %s' - found Name '%s'" % (
                target_package, target_package, new_info['name'])
            return False

        if not self.check_service_file(target_package):
            return False

        if not self.check_rpmlint(target_package):
            return False

        specs = [os.path.basename(x) for x in glob.glob(os.path.join(target_package, "*.spec"))]
        if not specs:
            # package without spec files e.g kiwi only
            return True

        if not self.check_spec_policy(target_package, specs):
            return False

        # Run check_source.pl script and interpret output.
        source_checker = os.path.join(CheckSource.SCRIPT_PATH, 'check_source.pl')
        civs = ''
        new_version = None
        if old_info['version'] and old_info['version'] != new_info['version']:
            new_version = new_info['version']
            civs += "NEW_VERSION='{}' ".format(new_version)
        civs += 'LC_ALL=C perl %s _old %s 2>&1' % (source_checker, target_package)
        p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, close_fds=True)
        ret = os.waitpid(p.pid, 0)[1]
        checked = decode_list(p.stdout.readlines())

        output = '  '.join(checked).replace('\033', '')
        os.chdir('/tmp')

        # ret = 0 : Good
        # ret = 1 : Bad
        # ret = 2 : Bad but can be non-fatal in some cases
        if ret > 1 and target_project.startswith('openSUSE:Leap:') and (source_project.startswith('SUSE:SLE-15:') or
                                                                        source_project.startswith('openSUSE:Factory')):
            pass
        elif ret != 0:
            shutil.rmtree(dir)
            self.review_messages['declined'] = "Output of check script:\n" + output
            return False

        shutil.rmtree(dir)
        self.review_messages['accepted'] = 'Check script succeeded'

        if len(checked):
            self.review_messages['accepted'] += "\n\nOutput of check script (non-fatal):\n" + output

        if not self.skip_add_reviews:
            if self.add_review_team and self.review_team is not None:
                self.add_review(self.request, by_group=self.review_team, msg='Please review sources')

            if self.only_changes():
                self.logger.debug('only .changes modifications')
                if self.staging_group and self.review_user in group_members(self.apiurl, self.staging_group):
                    if not self.dryrun:
                        osc.core.change_review_state(self.apiurl, str(self.request.reqid), 'accepted',
                                                     by_group=self.staging_group,
                                                     message='skipping the staging process since only .changes modifications')
                else:
                    self.logger.debug('unable to skip staging review since not a member of staging group')

        return True

    def is_devel_project(self, source_project, target_project):
        if source_project in self.devel_whitelist:
            return True

        # Allow any projects already used as devel projects for other packages.
        search = {
            'package': "@project='%s' and devel/@project='%s'" % (target_project, source_project),
        }
        result = osc.core.search(self.apiurl, **search)
        return result['package'].attrib['matches'] != '0'

    def check_service_file(self, directory):
        ALLOWED_MODES = ['localonly', 'disabled', 'buildtime', 'manual']

        servicefile = os.path.join(directory, '_service')
        if os.path.exists(servicefile):
            services = ET.parse(servicefile)
            for service in services.findall('service'):
                mode = service.get('mode')
                if mode in ALLOWED_MODES:
                    continue
                allowed = ', '.join(ALLOWED_MODES)
                name = service.get('name')
                self.review_messages[
                    'declined'] = f"Services are only allowed if their mode is one of {allowed}. " + \
                    f"Please change the mode of {name} and use `osc service localrun/disabledrun`."
                return False
            # remove it away to have full service from source validator
            os.unlink(servicefile)

        for file in glob.glob(os.path.join(directory, "_service:*")):
            file = os.path.basename(file)
            self.review_messages['declined'] = f"Found _service generated file {file} in checkout. Please clean this up first."
            return False

        return True

    def check_rpmlint(self, directory):
        for rpmlintrc in glob.glob(os.path.join(directory, "*rpmlintrc")):
            with open(rpmlintrc, 'r') as f:
                for line in f:
                    if not re.match(r'^\s*setBadness', line):
                        continue
                    self.review_messages['declined'] = f"For product submissions, you cannot use setBadness. Use filters in {rpmlintrc}."
                    return False
        return True

    def check_spec_policy(self, directory, specs):
        bname = os.path.basename(directory)
        if not os.path.exists(os.path.join(directory, bname + '.changes')):
            text = f"{bname}.changes is missing. "
            text += "A package submitted as FooBar needs to have a FooBar.changes file with a format created by `osc vc`."
            self.review_messages['declined'] = text
            return False

        specfile = os.path.join(directory, bname + '.spec')
        if not os.path.exists(specfile):
            self.review_messages['declined'] = f"{bname}.spec is missing. A package submitted as FooBar needs to have a FooBar.spec file."
            return False

        for spec in specs:
            with open(os.path.join(directory, spec), 'r') as f:
                content = f.read()
                if not re.search(r'#[*\s]+Copyright\s', content):
                    text = f"{spec} does not appear to contain a Copyright comment. Please stick to the format\n\n"
                    text += "# Copyright (c) 2022 Unsong Hero\n\n"
                    text += "or use osc service runall format_spec_file"
                    self.review_messages['declined'] = text
                    return False

                if re.search(r'\nVendor:', content):
                    self.review_messages['declined'] = "{spec} contains a Vendor line, this is forbidden."
                    return False

        return True

    def source_has_correct_maintainers(self, source_project):
        """Checks whether the source project has the required maintainer

        If a 'required-source-maintainer' is set, it checks whether it is a
        maintainer for the source project. Inherited maintainership is
        intentionally ignored to have explicit maintainer set.

        source_project - source project name
        """
        self.logger.info(
            'Checking required maintainer from the source project (%s)' % self.required_maintainer
        )
        if not self.required_maintainer:
            return True

        meta = ET.fromstringlist(show_project_meta(self.apiurl, source_project))
        maintainers = meta.xpath('//person[@role="maintainer"]/@userid')
        maintainers += ['group:' + g for g in meta.xpath('//group[@role="maintainer"]/@groupid')]

        return self.required_maintainer in maintainers

    def __ensure_add_role_request(self, source_project):
        """Returns add_role request ID for given source project. Creates that add role if needed."""
        try:
            add_roles = get_request_list(self.apiurl, source_project,
                                         req_state=['new', 'review'], req_type='add_role')
            add_roles = list(filter(self.__is_required_maintainer, add_roles))
            if len(add_roles) > 0:
                return add_roles[0].reqid
            else:
                add_role_msg = 'Created automatically from request %s' % self.request.reqid
                return create_add_role_request(self.apiurl, source_project, self.required_maintainer,
                                               'maintainer', message=add_role_msg)
        except HTTPError as e:
            self.logger.error(
                'Cannot create the corresponding add_role request for %s: %s' % (self.request.reqid, e)
            )

    def __is_required_maintainer(self, request):
        """Returns true for add role requests that adds required maintainer user or group"""
        action = request.actions[0]
        user = self.required_maintainer
        if user.startswith('group:'):
            group = user.replace('group:', '')
            return action.group_name == group and action.group_role == 'maintainer'
        else:
            return action.person_name == user and action.person_role == 'maintainer'

    @staticmethod
    def checkout_package(*args, **kwargs):
        _stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            result = osc.core.checkout_package(*args, **kwargs)
        finally:
            sys.stdout = _stdout
        return result

    def _package_source_parse(self, project, package, revision=None, repository=None):
        query = {'view': 'info', 'parse': 1}
        if revision:
            query['rev'] = revision
        if repository:
            query['repository'] = repository
        url = osc.core.makeurl(self.apiurl, ['source', project, package], query)

        ret = {'name': None, 'version': None}

        try:
            xml = ET.parse(osc.core.http_GET(url)).getroot()
        except HTTPError as e:
            self.logger.error('ERROR in URL %s [%s]' % (url, e))
            return ret

        if xml.find('error') is not None:
            self.logger.error("%s/%s/%s: %s", project, package, repository, xml.find('error').text)
            return ret

        # ET boolean check fails.
        if xml.find('name') is not None:
            ret['name'] = xml.find('name').text

        if xml.find('version') is not None:
            ret['version'] = xml.find('version').text

        if xml.find('filename') is not None:
            ret['filename'] = xml.find('filename').text

        self.logger.debug("%s/%s/%s: %s", project, package, repository, ret)

        return ret

    def only_changes(self):
        u = osc.core.makeurl(self.apiurl, ['request', self.request.reqid],
                             {'cmd': 'diff', 'view': 'xml'})
        try:
            diff = ET.parse(osc.core.http_POST(u)).getroot()
            for f in diff.findall('action/sourcediff/files/file/*[@name]'):
                if not f.get('name').endswith('.changes'):
                    return False
            return True
        except HTTPError:
            pass
        return False

    def check_action_add_role(self, request, action):
        # Decline add_role request (assumed the bot acting on requests to Factory or similar).
        message = 'Roles to packages are granted in the devel project, not in %s.' % action.tgt_project

        if action.tgt_package is not None:
            project, package = devel_project_fallback(self.apiurl, action.tgt_project, action.tgt_package)
            message += ' Send this request to {}/{}.'.format(project, package)

        self.review_messages['declined'] = message
        return False

    def check_action_delete_package(self, request, action):
        self.target_project_config(action.tgt_project)

        try:
            result = osc.core.show_project_sourceinfo(self.apiurl, action.tgt_project, True, (action.tgt_package))
            root = ET.fromstring(result)
        except HTTPError:
            return None

        # Decline the delete request if there is another delete/submit request against the same package
        query = "match=state/@name='new'+and+(action/target/@project='{}'+and+action/target/@package='{}')"\
                "+and+(action/@type='delete'+or+action/@type='submit')".format(action.tgt_project, action.tgt_package)
        url = osc.core.makeurl(self.apiurl, ['search', 'request'], query)
        matches = ET.parse(osc.core.http_GET(url)).getroot()
        if int(matches.attrib['matches']) > 1:
            ids = [rq.attrib['id'] for rq in matches.findall('request')]
            self.review_messages['declined'] = "There is a pending request %s to %s/%s in process." % (
                ','.join(ids), action.tgt_project, action.tgt_package)
            return False

        # Decline delete requests against linked flavor package
        linked = root.find('sourceinfo/linked')
        if not (linked is None or self.check_linked_package(action, linked)):
            return False

        if not self.ignore_devel:
            self.devel_project_review_ensure(request, action.tgt_project, action.tgt_package)

        return True

    def check_linked_package(self, action, linked):
        if linked.get('project', action.tgt_project) != action.tgt_project:
            return True
        linked_package = linked.get('package')
        self.review_messages['declined'] = "Delete the package %s instead" % (linked_package)
        return False

    def check_action_delete_project(self, request, action):
        # Presumably if the request is valid the bot should be disabled or
        # overridden, but seems like no valid case for allowing this (see #1696).
        self.review_messages['declined'] = 'Deleting the {} project is not allowed.'.format(action.tgt_project)
        return False

    def check_action_delete_repository(self, request, action):
        self.target_project_config(action.tgt_project)

        if self.mail_release_list:
            self.review_messages['declined'] = 'Deleting repositories is not allowed. ' \
                'Contact {} to discuss further.'.format(self.mail_release_list)
            return False

        self.review_messages['accepted'] = 'unhandled: removing repository'
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = CheckSource

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('--skip-add-reviews', action='store_true', default=False,
                          help='skip adding review after completing checks')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        bot.skip_add_reviews = self.options.skip_add_reviews

        return bot


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
