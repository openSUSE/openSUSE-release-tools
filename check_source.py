#!/usr/bin/python3

import difflib
import glob
import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Optional, Set
from cmdln import CmdlnOptionParser

from lxml import etree as ET

import osc.core
from urllib3.exceptions import MaxRetryError
from osclib.core import devel_project_get, factory_git_devel_project_mapping
from osclib.core import devel_project_fallback
from osclib.core import entity_exists
from osclib.core import group_members
from osclib.core import package_kind
from osclib.core import create_add_role_request
from osclib.core import package_role_expand
from osclib.core import source_file_load
from osclib.core import project_pseudometa_package
from osc.core import show_package_meta, show_project_meta
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

    def target_project_config(self, project: str) -> None:
        # Load project config and allow for remote entries.
        config = self.platform.get_project_config(project)

        self.single_action_require = str2bool(config.get('check-source-single-action-require', 'False'))
        self.ignore_devel: bool = not str2bool(config.get('devel-project-enforce', 'False'))
        self.in_air_rename_allow = str2bool(config.get('check-source-in-air-rename-allow', 'False'))
        self.add_review_team = str2bool(config.get('check-source-add-review-team', 'True'))
        self.review_team = config.get('review-team')
        self.mail_release_list = config.get('mail-release-list')
        self.staging_group = config.get('staging-group')
        self.required_maintainer = config.get('required-source-maintainer', '')
        self.devel_whitelist = config.get('devel-whitelist', '').split()
        self.skip_add_reviews = False
        self.ensure_source_exist_in_baseproject = str2bool(config.get('check-source-ensure-source-exist-in-baseproject', 'False'))
        self.devel_baseproject: str = config.get('check-source-devel-baseproject', '')
        self.allow_source_in_sle = str2bool(config.get('check-source-allow-source-in-sle', 'True'))
        self.sle_project_to_check = config.get('check-source-sle-project', '')
        self.slfo_packagelist_to_check = config.get('check-source-slfo-packagelist-file', '')
        self.allow_valid_source_origin = str2bool(config.get('check-source-allow-valid-source-origin', 'False'))
        self.valid_source_origins: Set[str] = set(config.get('check-source-valid-source-origins', '').split(' '))
        self.add_devel_project_review = str2bool(config.get('check-source-add-devel-project-review', 'False'))
        self.allowed_scm_submission_sources = config.get('allowed-scm-submission-sources', '').split()

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

    def is_good_name(self, package: Optional[str], target_package: Optional[str]) -> bool:
        self.logger.debug(f"is_good_name {package} <-> {target_package}")
        if target_package is None:
            # if the name doesn't matter, existance is all
            return package is not None

        return target_package == package

    def package_source_parse(self, project, package, revision=None, target_package=None):
        # XXX should we refactor this out?
        if self.platform_type == "OBS":
            return self._package_source_parse_obs(project, package, revision, target_package)
        else:
            # TODO source_info API is not available on Gitea.
            # This is a temporary mock, need to implement a better one
            self.logger.warning("package_source_parse() is currently mocked on this platform.")
            return {
                "name": target_package,
                "revision": revision, "filename":
                f"{target_package}.spec"
            }

    def _package_source_parse_obs(self, project, package, revision=None, target_package=None):
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

    def check_source_submission(
            self,
            source_project: str,
            source_package: str,
            source_revision: str,
            target_project: str,
            target_package: str
    ) -> bool:
        super(CheckSource, self).check_source_submission(source_project,
                                                         source_package, source_revision, target_project, target_package)
        self.target_project_config(target_project)

        if self.single_action_require and len(self.request.actions) != 1:
            self.review_messages['declined'] = 'Only one action per request allowed'
            return False

        if source_revision is None:
            self.review_messages['declined'] = 'Submission not from a pinned source revision'
            return False

        # XXX refactor this out
        if self.platform_type == "OBS":
            kind = package_kind(self.apiurl, target_project, target_package)
        else:
            # XXX stub
            kind = 'source'

        if kind == 'meta' or kind == 'patchinfo':
            self.review_messages['accepted'] = f'Skipping most checks for {kind} packages'
            if not self.skip_add_reviews and self.add_review_team and self.review_team is not None:
                if not (self.allow_valid_source_origin and source_project in self.valid_source_origins):
                    self.add_review(self.request, by_group=self.review_team, msg='Please review sources')
            return True
        elif (kind is not None and kind != 'source'):
            self.review_messages['declined'] = f'May not modify a non-source package of type {kind}'
            return False

        if not self.allow_source_in_sle:
            if self.sle_project_to_check and entity_exists(self.apiurl, self.sle_project_to_check, target_package):
                self.review_messages['declined'] = ("SLE-base package, please submit to the corresponding SLE project."
                                                    "Or let us know the reason why needs to rebuild SLE-base package.")
                return False
            if self.slfo_packagelist_to_check:
                pseudometa_project, pseudometa_package = project_pseudometa_package(self.apiurl, target_project)
                if pseudometa_project and pseudometa_package:
                    metafile = ET.fromstring(source_file_load(self.apiurl, pseudometa_project, pseudometa_package,
                                                              self.slfo_packagelist_to_check))
                    slfo_pkglist = [package.attrib['name'] for package in metafile.findall('package')]
                    if target_package in slfo_pkglist:
                        self.review_messages['declined'] = ("Please create a new feature request "
                                                            f"https://code.opensuse.org/leap/features/issues for updating {target_package} "
                                                            "from SLFO (SLES, SL Micro). Alternatively, please provide "
                                                            "a reason for forking and rebuilding an existing SLFO package in Leap.")
                        return False

        if self.ensure_source_exist_in_baseproject and self.devel_baseproject:
            if not entity_exists(self.apiurl, self.devel_baseproject, target_package) and source_project not in self.valid_source_origins:
                self.review_messages['declined'] = f"Per our development policy, please submit to {self.devel_baseproject} first."
                return False

        inair_renamed = target_package != source_package

        if not self.ignore_devel:
            self.logger.info('checking if target package exists and has devel project')
            devel_project, devel_package = devel_project_get(self.apiurl, target_project, target_package)
            if devel_project:
                if (
                        (source_project != devel_project or source_package != devel_package)
                        and not (source_project == target_project and source_package == target_package)):
                    # check if the devel project & package are using scmsync & match the allowed prj prefix
                    # => waive the devel project source submission requirement
                    meta = ET.fromstringlist(show_package_meta(self.apiurl, devel_project, devel_package))
                    scm_sync = meta.find('scmsync')
                    if scm_sync is None:
                        # Not from proper devel project/package and not self-submission and not scmsync.
                        self.review_messages['declined'] = f'Expected submission from devel package {devel_project}/{devel_package}'
                        return False

                    scm_pool_repository = f"https://src.opensuse.org/pool/{source_package}"
                    if not scm_sync.text.startswith(scm_pool_repository):
                        # devel project uses scm sync not from the trusted src location
                        self.review_messages['declined'] = (
                            f"devel project scmsync setting is {scm_sync.text}. Must be {scm_pool_repository} instead.")
                        return False
                    if not self.source_is_scm_staging_submission(source_project):
                        # Not a submission coming from the scm-sync bot
                        self.review_messages['declined'] = "Expected a submitrequest coming from scm-sync project"
                        return False

            else:
                # Check to see if other packages exist with the same source project
                # which indicates that the project has already been used as devel.
                if not self.is_devel_project(source_project, target_project):
                    self.review_messages['declined'] = (
                        f'{source_project} is not a devel project of {target_project}, submit the package to a devel project first. '
                        'See https://en.opensuse.org/openSUSE:How_to_contribute_to_Factory#How_to_request_a_new_devel_project for details.'
                    )
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

        # TODO(dmllr): ensure requird maintainers are set in the temporary project that is created
        # by the scm-staging bot
        if not self.source_is_scm_staging_submission(source_project) and not self.source_has_required_maintainers(source_project):
            declined_msg = (
                f'This request cannot be accepted unless {self.required_maintainer} is a maintainer of {source_project}.'
            )

            req = self.__ensure_add_role_request(source_project)
            if req:
                declined_msg += f' Created the add_role request {req} for addressing this problem.'

            self.review_messages['declined'] = declined_msg
            return False

        if not self.in_air_rename_allow and inair_renamed:
            self.review_messages['declined'] = 'Source and target package names must match'
            return False

        # Checkout and see if renaming package screws up version parsing.
        copath = os.path.expanduser(f'~/co/{self.request.reqid}')
        if os.path.exists(copath):
            self.logger.warning(f'directory {copath} already exists')
            shutil.rmtree(copath)
        os.makedirs(copath)
        os.chdir(copath)

        try:
            CheckSource.checkout_package(self.scm, target_project, target_package, pathname=copath,
                                         server_service_files=True, expand_link=True)
            os.rename(target_package, '_old')
        except HTTPError as e:
            if e.code == 404:
                self.logger.info(f'target package does not exist {target_project}/{target_package}')
            else:
                raise e

        CheckSource.checkout_package(self.scm, source_project, source_package, revision=source_revision,
                                     pathname=copath, server_service_files=True, expand_link=True)
        os.rename(source_package, target_package)

        new_info = self.package_source_parse(source_project, source_package, source_revision, target_package)
        filename = new_info.get('filename', '')
        expected_name = target_package
        if filename == '_preinstallimage':
            expected_name = 'preinstallimage'
        if not (filename.endswith('.kiwi') or filename == 'Dockerfile') and new_info['name'] != expected_name:
            shutil.rmtree(copath)
            self.review_messages['declined'] = (
                f"A package submitted as {target_package} has to build as 'Name: {expected_name}' - found Name '{new_info['name']}'")
            return False

        if not self.check_service_file(target_package):
            return False

        if not self.check_rpmlint(target_package):
            return False

        specs = [os.path.basename(x) for x in glob.glob(os.path.join(target_package, "*.spec"))]
        if specs and not self.check_spec_policy('_old', target_package, specs):
            return False

        if not self.run_source_validator('_old', target_package):
            return False

        if specs and not self.detect_mentioned_patches('_old', target_package, specs):
            return False

        if not self.check_urls('_old', target_package, specs):
            if self.platform_type == "OBS":
                # Keep review open
                self.platform.change_review_state(req=self.request, newstate='new',
                                                  by_group=self.review_group,
                                                  by_user=self.review_user, message=self.review_messages['new'])
                return None
            else:
                return False

        shutil.rmtree(copath)
        self.review_messages['accepted'] = 'Check script succeeded'

        if self.skip_add_reviews:
            return True

        if self.add_review_team and self.review_team is not None:
            if not (self.allow_valid_source_origin and source_project in self.valid_source_origins):
                self.add_review(self.request, by_group=self.review_team, msg='Please review sources')

        if self.add_devel_project_review:
            devel_project, devel_package = devel_project_fallback(self.apiurl, target_project, target_package)
            if devel_project and devel_package:
                submitter = self.request.creator
                maintainers = set(package_role_expand(self.apiurl, devel_project, devel_package))
                known_maintainer = False
                if maintainers:
                    if submitter in maintainers:
                        self.logger.debug(f"{submitter} is maintainer")
                        known_maintainer = True
                    if not known_maintainer:
                        for r in self.request.reviews:
                            if r.by_user in maintainers:
                                self.logger.debug(f"found {r.by_user} as reviewer")
                                known_maintainer = True
                if not known_maintainer:
                    self.logger.warning(f"submitter: {submitter}, maintainers: {','.join(maintainers)} => need review")
                    self.logger.debug(f"adding review to {devel_project}/{devel_package}")
                    msg = ('Submission for {} by someone who is not maintainer in '
                           'the devel project ({}). Please review').format(target_package, devel_project)

                    if self.is_scmsync(devel_project):
                        self.add_review(self.request, by_project=devel_project, msg=msg)
                    else:
                        self.add_review(self.request, by_project=devel_project, by_package=devel_package, msg=msg)
            else:
                self.logger.warning(f"{target_package} doesn't have devel project")

        if self.only_changes():
            self.logger.debug('only .changes modifications')
            if self.staging_group and self.review_user in group_members(self.apiurl, self.staging_group):
                self.review_messages["accepted"] = 'skipping the staging process since only .changes modifications'
                return True
            else:
                self.logger.debug('unable to skip staging review since not a member of staging group')

        return True

    def is_devel_project(self, source_project, target_project):
        if source_project in self.devel_whitelist:
            return True

        # Allow any projects already used as devel projects for other packages.
        if target_project.endswith('openSUSE:Factory'):
            devel_pkgs = factory_git_devel_project_mapping(self.apiurl)
            return True if source_project in devel_pkgs.values() else False

        search = {
            'package': f"@project='{target_project}' and devel/@project='{source_project}'",
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

    def check_spec_policy(self, old, directory, specs):
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

        changes_updated = False
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

                if not re.search(r'\n%changelog\s', content) and not re.search(r'\n%changelog$', content):
                    text = f"{spec} does not contain a %changelog line. We don't want a changelog in the spec file"
                    text += ", but the %changelog section needs to be present\n"
                    self.review_messages['declined'] = text
                    return False

                if not re.search('#[^\n]*license', content, flags=re.IGNORECASE):
                    text = f"{spec} does not appear to have a license. The file needs to contain a free software license\n"
                    text += "Suggestion: use \"osc service runall format_spec_file\" to get our default license or\n"
                    text += "the minimal license:\n\n"
                    text += "# This file is under MIT license\n"
                    self.review_messages['declined'] = text
                    return False

            # Check that we have for each spec file a changes file - and that at least one
            # contains changes
            changes = spec.replace('.spec', '.changes')

            # new or deleted .changes files also count
            old_exists = os.path.exists(os.path.join(old, changes))
            new_exists = os.path.exists(os.path.join(directory, changes))
            if old_exists != new_exists:
                changes_updated = True
            elif old_exists and new_exists:
                if subprocess.run(["cmp", "-s", os.path.join(old, changes), os.path.join(directory, changes)]).returncode:
                    changes_updated = True

        if not changes_updated:
            self.review_messages['declined'] = "No changelog. Please use 'osc vc' to update the changes file(s)."
            return False

        return True

    def source_is_scm_staging_submission(self, source_project):
        """Checks whether the source project is a scm_submission source project"""

        return any(source_project.startswith(allowed_src) for allowed_src in self.allowed_scm_submission_sources)

    def source_has_required_maintainers(self, source_project):
        """Checks whether the source project has the required maintainer

        If a 'required-source-maintainer' is set, it checks whether it is a
        maintainer for the source project. Inherited maintainership is
        intentionally ignored to have explicit maintainer set.

        source_project - source project name
        """
        self.logger.info(
            f'Checking required maintainer from the source project ({self.required_maintainer})'
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
                add_role_msg = f'Created automatically from request {self.request.reqid}'
                return create_add_role_request(self.apiurl, source_project, self.required_maintainer,
                                               'maintainer', message=add_role_msg)
        except HTTPError as e:
            self.logger.error(
                f'Cannot create the corresponding add_role request for {self.request.reqid}: {e}'
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
    def checkout_package(scm, target_project, target_package, pathname, **kwargs):
        return scm.checkout_package(
            target_project,
            target_package,
            pathname,
            **kwargs
        )

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
            self.logger.error(f'ERROR in URL {url} [{e}]')
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
        if self.platform_type != "OBS":
            self.logger.warning("skipping only_changes check on this platform")
            return False

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
        except MaxRetryError:
            pass
        return False

    def check_action_add_role(self, request, action):
        # Decline add_role request (assumed the bot acting on requests to Factory or similar).
        message = f'Roles to packages are granted in the devel project, not in {action.tgt_project}.'

        if action.tgt_package is not None:
            project, package = devel_project_fallback(self.apiurl, action.tgt_project, action.tgt_package)
            message += f' Send this request to {project}/{package}.'

        self.review_messages['declined'] = message
        return False

    def check_action_delete_package(self, request, action):
        self.target_project_config(action.tgt_project)

        try:
            result = osc.core.show_project_sourceinfo(self.apiurl, action.tgt_project, True, (action.tgt_package))
            root = ET.fromstring(result)
        except HTTPError:
            return None
        except MaxRetryError:
            return None

        # Decline the delete request if there is another delete/submit request against the same package
        query = "match=state/@name='new'+and+(action/target/@project='{}'+and+action/target/@package='{}')"\
                "+and+(action/@type='delete'+or+action/@type='submit')".format(action.tgt_project, action.tgt_package)
        url = osc.core.makeurl(self.apiurl, ['search', 'request'], query)
        matches = ET.parse(osc.core.http_GET(url)).getroot()
        if int(matches.attrib['matches']) > 1:
            ids = [rq.attrib['id'] for rq in matches.findall('request')]
            self.review_messages['declined'] = (
                f"There is a pending request {','.join(ids)} to {action.tgt_project}/{action.tgt_package} in process.")
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
        self.review_messages['declined'] = f"Delete the package {linked_package} instead"
        return False

    def check_action_delete_project(self, request, action):
        # Presumably if the request is valid the bot should be disabled or
        # overridden, but seems like no valid case for allowing this (see #1696).
        self.review_messages['declined'] = f'Deleting the {action.tgt_project} project is not allowed.'
        return False

    def check_action_delete_repository(self, request, action):
        self.target_project_config(action.tgt_project)

        if self.mail_release_list:
            self.review_messages['declined'] = 'Deleting repositories is not allowed. ' \
                'Contact {} to discuss further.'.format(self.mail_release_list)
            return False

        self.review_messages['accepted'] = 'unhandled: removing repository'
        return True

    def run_source_validator(self, old, directory):
        scripts = glob.glob("/usr/lib/obs/service/source_validators/*")
        if not scripts:
            raise RuntimeError.new('Missing source validator')
        for script in scripts:
            if os.path.isdir(script):
                continue
            res = subprocess.run([script, '--batchmode', directory, old], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if res.returncode:
                text = "Source validator failed. Try \"osc service runall source_validator\"\n"
                text += res.stdout.decode('utf-8')
                self.review_messages['declined'] = text
                return False

            for line in res.stdout.decode('utf-8').split("\n"):
                # pimp up some warnings
                if re.search(r'Attention.*not mentioned', line):
                    line = re.sub(r'\(W\) ', '', line)
                    self.review_messages['declined'] = line
                    return False

        return True

    def _snipe_out_existing_urls(self, old, directory, specs):
        if not os.path.isdir(old):
            return
        oldsources = self._mentioned_sources(old, specs)
        for spec in specs:
            specfn = os.path.join(directory, spec)
            nspecfn = specfn + '.new'
            wf = open(nspecfn, 'w')
            with open(specfn) as rf:
                for line in rf:
                    m = re.match(r'(Source[0-9]*\s*):\s*(.*)$', line)
                    if m and m.group(2) in oldsources:
                        wf.write(m.group(1) + ":" + os.path.basename(m.group(2)) + "\n")
                        continue
                    wf.write(line)
            wf.close()
            os.rename(nspecfn, specfn)

    def check_urls(self, old, directory, specs):
        self._snipe_out_existing_urls(old, directory, specs)
        oldcwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(directory)
            res = subprocess.run(["/usr/lib/obs/service/download_files", "--enforceupstream",
                                  "yes", "--enforcelocal", "yes", "--outdir", tmpdir], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            if res.returncode:
                review_message = "Source URLs are not valid. Try `osc service runall download_files`.\n" + \
                    res.stdout.decode('utf-8')
                if self.platform_type == "OBS":
                    self.review_messages["new"] = review_message
                else:
                    self.review_messages["declined"] = review_message
                os.chdir(oldcwd)
                return False
        os.chdir(oldcwd)
        return True

    def difflines(self, oldf, newf):
        with open(oldf, 'r') as f:
            oldl = f.readlines()
        with open(newf, 'r') as f:
            newl = f.readlines()
        return list(difflib.unified_diff(oldl, newl))

    def _mentioned_sources(self, directory, specs):
        sources = set()
        for spec in specs:
            specfn = os.path.join(directory, spec)
            if not os.path.exists(specfn):
                continue
            with open(specfn) as f:
                for line in f:
                    m = re.match(r'Source[0-9]*\s*:\s*(.*)$', line)
                    if not m:
                        continue
                    sources.add(m.group(1))
        return sources

    def detect_mentioned_patches(self, old, directory, specs):
        # new packages have different rules
        if not os.path.isdir(old):
            return True
        opatches = self.list_patches(old)
        npatches = self.list_patches(directory)

        cpatches = opatches.intersection(npatches)
        opatches -= cpatches
        npatches -= cpatches

        if not npatches and not opatches:
            return True

        patches_to_mention = {}
        for p in opatches:
            patches_to_mention[p] = 'old'
        for p in npatches:
            patches_to_mention[p] = 'new'
        for changes in glob.glob(os.path.join(directory, '*.changes')):
            base = os.path.basename(changes)
            oldchanges = os.path.join(old, base)
            if os.path.exists(oldchanges):
                diff = self.difflines(oldchanges, changes)
            else:
                with open(changes, 'r') as f:
                    diff = ['+' + line for line in f.readlines()]
            for line in diff:
                pass
                # Check if the line mentions a patch being added (starts with +)
                # or removed (starts with -)
                if not re.match(r'[+-]', line):
                    continue
                # In any of those cases, remove the patch from the list
                line = line[1:].strip()
                for patch in list(patches_to_mention):
                    if line.find(patch) >= 0:
                        del patches_to_mention[patch]

        # if a patch is mentioned as source, we ignore it
        sources = self._mentioned_sources(directory, specs)
        sources |= self._mentioned_sources(old, specs)

        for s in sources:
            patches_to_mention.pop(s, None)

        if not patches_to_mention:
            return True

        lines = []
        for patch, state in patches_to_mention.items():
            # wording stolen from Raymond's declines :)
            if state == 'new':
                lines.append(f"A patch ({patch}) is being added without this addition being mentioned in the changelog.")
            else:
                lines.append(f"A patch ({patch}) is being deleted without this removal being mentioned in the changelog.")
        self.review_messages['declined'] = '\n'.join(lines)
        return False

    def list_patches(self, directory):
        ret = set()
        for ext in ['*.diff', '*.patch', '*.dif']:
            for file in glob.glob(os.path.join(directory, ext)):
                ret.add(os.path.basename(file))
        return ret


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = CheckSource

    def get_optparser(self) -> CmdlnOptionParser:
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
