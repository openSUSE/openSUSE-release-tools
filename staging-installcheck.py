#!/usr/bin/python3

import argparse
import logging
import os
import re
import sys
from collections import namedtuple
from urllib.error import HTTPError

import osc.core
import yaml
from lxml import etree as ET

from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.conf import str2bool
from osclib.core import (builddepinfo, depends_on, duplicated_binaries_in_repo,
                         fileinfo_ext_all, repository_arch_state,
                         repository_path_expand, target_archs)

from osclib.repochecks import installcheck, mirror
from osclib.stagingapi import StagingAPI
from osclib.memoize import memoize

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))


class InstallChecker(object):
    def __init__(self, api, config):
        self.api = api
        self.logger = logging.getLogger('InstallChecker')
        self.commentapi = CommentAPI(api.apiurl)

        self.arch_whitelist = config.get('repo_checker-arch-whitelist')
        if self.arch_whitelist:
            self.arch_whitelist = set(self.arch_whitelist.split(' '))

        self.ring_whitelist = set(config.get('repo_checker-binary-whitelist-ring', '').split(' '))

        self.cycle_packages = config.get('repo_checker-allowed-in-cycles')
        self.calculate_allowed_cycles()

        self.ignore_duplicated = set(config.get('installcheck-ignore-duplicated-binaries', '').split(' '))
        self.ignore_conflicts = set(config.get('installcheck-ignore-conflicts', '').split(' '))
        self.ignore_deletes = str2bool(config.get('installcheck-ignore-deletes', 'False'))

    def check_required_by(self, fileinfo, provides, requiredby, built_binaries, comments):
        if requiredby.get('name') in built_binaries:
            return True

        result = True

        # In some cases (boolean deps?) it's possible that fileinfo_ext for A
        # shows that A provides cap needed by B, but fileinfo_ext for B does
        # not list cap or A at all... In that case better error out and ask for
        # human intervention.
        dep_found = False
        # In case the dep was not found, give a hint what OBS might have meant.
        possible_dep = None

        # extract >= and the like
        provide = provides.get('dep')
        provide = provide.split(' ')[0]
        comments.append('{} provides {} required by {}'.format(
            fileinfo.find('name').text, provide, requiredby.get('name')))
        url = api.makeurl(['build', api.project, api.cmain_repo, 'x86_64', '_repository', requiredby.get('name') + '.rpm'],
                          {'view': 'fileinfo_ext'})
        reverse_fileinfo = ET.parse(osc.core.http_GET(url)).getroot()

        for require in reverse_fileinfo.findall('requires_ext'):
            # extract >= and the like here too
            dep = require.get('dep').split(' ')[0]
            if dep != provide:
                if provide in require.get('dep'):
                    possible_dep = require.get('dep')
                continue
            dep_found = True
            # Whether this is provided by something being deleted
            provided_found = False
            # Whether this is provided by something not being deleted
            alternative_found = False
            for provided_by in require.findall('providedby'):
                if provided_by.get('name') in built_binaries:
                    provided_found = True
                else:
                    comments.append(f"  also provided by {provided_by.get('name')} -> ignoring")
                    alternative_found = True

            if not alternative_found:
                result = False

            if not provided_found:
                comments.append("  OBS doesn't see this in the reverse resolution though. Not sure what to do.")
                result = False

        if not dep_found:
            comments.append("  OBS doesn't see this dep in reverse though. Not sure what to do.")
            if possible_dep is not None:
                comments.append(f'  Might be required by {possible_dep}')
            return False

        if result:
            return True
        else:
            comments.append(f'Error: missing alternative provides for {provide}')
            return False

    @memoize(session=True)
    def pkg_with_multibuild_flavors(self, package):
        ret = set([package])
        # Add all multibuild flavors
        mainprjresult = ET.fromstringlist(osc.core.show_results_meta(self.api.apiurl, self.api.project, multibuild=True))
        for pkg in mainprjresult.xpath(f"result/status[starts-with(@package,'{package}:')]"):
            ret.add(pkg.get('package'))

        return ret

    def check_delete_request(self, req, to_ignore, to_delete, comments):
        package = req.get('package')
        if package in to_ignore or self.ignore_deletes:
            self.logger.info(f'Delete request for package {package} ignored')
            return True

        pkg_flavors = self.pkg_with_multibuild_flavors(package)

        built_binaries = set()
        file_infos = []
        for flavor in pkg_flavors:
            for fileinfo in fileinfo_ext_all(self.api.apiurl, self.api.project, self.api.cmain_repo, 'x86_64', flavor):
                built_binaries.add(fileinfo.find('name').text)
                file_infos.append(fileinfo)
        # extend the others - this asks for a refactoring, but we don't handle tons of delete requests often
        for ptd in to_delete:
            if ptd in pkg_flavors:
                continue
            for fileinfo in fileinfo_ext_all(self.api.apiurl, self.api.project, self.api.cmain_repo, 'x86_64', ptd):
                built_binaries.add(fileinfo.find('name').text)

        result = True
        for fileinfo in file_infos:
            for provides in fileinfo.findall('provides_ext'):
                for requiredby in provides.findall('requiredby[@name]'):
                    result = result and self.check_required_by(fileinfo, provides, requiredby, built_binaries, comments)

        what_depends_on = depends_on(api.apiurl, api.project, api.cmain_repo, pkg_flavors, True)

        # filter out packages to be deleted
        for ptd in to_delete:
            if ptd in what_depends_on:
                what_depends_on.remove(ptd)

        if len(what_depends_on):
            comments.append('{} is still a build requirement of:\n\n- {}'.format(
                package, '\n- '.join(sorted(what_depends_on))))
            return False

        return result

    def packages_to_ignore(self, project):
        comments = self.commentapi.get_comments(project_name=project)
        ignore_re = re.compile(r'^installcheck: ignore (?P<args>.*)$', re.MULTILINE)

        # the last wins, for now we don't care who said it
        args = []
        for comment in comments.values():
            match = ignore_re.search(comment['comment'].replace('\r', ''))
            if not match:
                continue
            args = match.group('args').strip()
            # allow space and comma to seperate
            args = args.replace(',', ' ').split(' ')
        return set(args)

    def staging(self, project, repository, force=False, devel=False):
        api = self.api

        # fetch the build ids at the beginning - mirroring takes a while
        buildids = {}
        try:
            architectures = self.target_archs(project, repository)
        except HTTPError as e:
            if e.code == 404:
                # adi disappear all the time, so don't worry
                return False
            raise e

        all_done = True
        for arch in architectures:
            pra = f'{project}/{repository}/{arch}'
            buildid = self.buildid(project, repository, arch)
            if not buildid:
                self.logger.error(f'No build ID in {pra}')
                return False
            buildids[arch] = buildid
            url = self.report_url(project, repository, arch, buildid)
            try:
                root = ET.parse(osc.core.http_GET(url)).getroot()
                check = root.find('check[@name="installcheck"]/state')
                if check is not None and check.text != 'pending':
                    self.logger.info(f'{pra} already "{check.text}", ignoring')
                else:
                    all_done = False
            except HTTPError:
                self.logger.info(f'{pra} has no status report')
                all_done = False

        if all_done and not force:
            return True

        repository_pairs = repository_path_expand(api.apiurl, project, repository)
        result_comment = []

        result = True
        to_ignore = self.packages_to_ignore(project)
        if not devel:
            status = api.project_status(project)
            if status is None:
                self.logger.error(f'no project status for {project}')
                return False

            # collect packages to be deleted
            to_delete = set()
            for req in status.findall('staged_requests/request'):
                if req.get('type') == 'delete':
                    to_delete |= self.pkg_with_multibuild_flavors(req.get('package'))

            for req in status.findall('staged_requests/request'):
                if req.get('type') == 'delete':
                    result = self.check_delete_request(req, to_ignore, to_delete, result_comment) and result

        for arch in architectures:
            # hit the first repository in the target project (if existant)
            target_pair = None
            directories = []
            for pair_project, pair_repository in repository_pairs:
                # ignore repositories only inherited for config
                if repository_arch_state(self.api.apiurl, pair_project, pair_repository, arch):
                    if not target_pair and pair_project == api.project:
                        target_pair = [pair_project, pair_repository]

                    directories.append(mirror(self.api.apiurl, pair_project, pair_repository, arch))

            if not api.is_adi_project(project):
                # For "leaky" ring packages in letter stagings, where the
                # repository setup does not include the target project, that are
                # not intended to to have all run-time dependencies satisfied.
                whitelist = self.ring_whitelist
            else:
                whitelist = set()

            whitelist |= to_ignore
            ignore_conflicts = self.ignore_conflicts | to_ignore

            check = self.cycle_check(project, repository, arch)
            if not check.success:
                self.logger.warning('Cycle check failed')
                result_comment.append(check.comment)
                result = False

            check = self.install_check(directories, arch, whitelist, ignore_conflicts)
            if not check.success:
                self.logger.warning('Install check failed')
                result_comment.append(check.comment)
                result = False

        duplicates = duplicated_binaries_in_repo(self.api.apiurl, project, repository)
        # remove white listed duplicates
        for arch in list(duplicates):
            for binary in self.ignore_duplicated:
                duplicates[arch].pop(binary, None)
            if not len(duplicates[arch]):
                del duplicates[arch]
        if len(duplicates):
            self.logger.warning('Found duplicated binaries')
            result_comment.append('Found duplicated binaries')
            result_comment.append(yaml.dump(duplicates, default_flow_style=False))
            result = False

        if devel:
            print(project, '\n'.join(result_comment))
        else:
            if result:
                self.report_state('success', self.gocd_url(), project, repository, buildids)
            else:
                result_comment.insert(0, f'Generated from {self.gocd_url()}\n')
                self.report_state('failure', self.upload_failure(project, result_comment, devel), project, repository, buildids)
                self.logger.warning(f'Not accepting {project}')
                return False

        return result

    def upload_failure(self, project, comment):
        print(project, '\n'.join(comment))
        url = self.api.makeurl(['source', 'home:repo-checker', 'reports', project])
        osc.core.http_PUT(url, data='\n'.join(comment))

        url = self.api.apiurl.replace('api.', 'build.')
        return f'{url}/package/view_file/home:repo-checker/reports/{project}'

    def report_state(self, state, report_url, project, repository, buildids):
        architectures = self.target_archs(project, repository)
        for arch in architectures:
            self.report_pipeline(state, report_url, project, repository, arch, buildids[arch])

    def gocd_url(self):
        if not os.environ.get('GO_SERVER_URL'):
            # placeholder :)
            return 'http://stephan.kulow.org/'
        report_url = os.environ.get('GO_SERVER_URL').replace(':8154', '')
        return report_url + '/tab/build/detail/{}/{}/{}/{}/{}#tab-console'.format(os.environ.get('GO_PIPELINE_NAME'),
                                                                                  os.environ.get('GO_PIPELINE_COUNTER'),
                                                                                  os.environ.get('GO_STAGE_NAME'),
                                                                                  os.environ.get('GO_STAGE_COUNTER'),
                                                                                  os.environ.get('GO_JOB_NAME'))

    def buildid(self, project, repository, architecture):
        url = self.api.makeurl(['build', project, repository, architecture], {'view': 'status'})
        root = ET.parse(osc.core.http_GET(url)).getroot()
        buildid = root.find('buildid')
        if buildid is None:
            return False
        return buildid.text

    def report_url(self, project, repository, architecture, buildid):
        return self.api.makeurl(['status_reports', 'built', project,
                                 repository, architecture, 'reports', buildid])

    def report_pipeline(self, state, report_url, project, repository, architecture, buildid):
        url = self.report_url(project, repository, architecture, buildid)
        name = 'installcheck'
        xml = self.check_xml(report_url, state, name)
        try:
            osc.core.http_POST(url, data=xml)
        except HTTPError:
            print('failed to post status to ' + url)
            sys.exit(1)

    def check_xml(self, url, state, name):
        check = ET.Element('check')
        if url:
            se = ET.SubElement(check, 'url')
            se.text = url
        se = ET.SubElement(check, 'state')
        se.text = state
        se = ET.SubElement(check, 'name')
        se.text = name
        return ET.tostring(check)

    def target_archs(self, project, repository):
        archs = target_archs(self.api.apiurl, project, repository)

        # Check for arch whitelist and use intersection.
        if self.arch_whitelist:
            archs = list(self.arch_whitelist.intersection(set(archs)))

        # Trick to prioritize x86_64.
        return sorted(archs, reverse=True)

    def install_check(self, directories, arch, whitelist, ignored_conflicts):
        self.logger.info(f"install check: start (whitelist:{','.join(whitelist)})")
        parts = installcheck(directories, arch, whitelist, ignored_conflicts)
        if len(parts):
            header = f'### [install check & file conflicts for {arch}]'
            return CheckResult(False, header + '\n\n' + ('\n' + ('-' * 80) + '\n\n').join(parts))

        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def calculate_allowed_cycles(self):
        self.allowed_cycles = []
        if self.cycle_packages:
            for comma_list in self.cycle_packages.split(';'):
                self.allowed_cycles.append(comma_list.split(','))

    def cycle_check(self, project, repository, arch):
        self.logger.info(f'cycle check: start {project}/{repository}/{arch}')
        comment = []

        depinfo = builddepinfo(self.api.apiurl, project, repository, arch, order=False)
        for cycle in depinfo.findall('cycle'):
            for package in cycle.findall('package'):
                package = package.text
                allowed = False
                for acycle in self.allowed_cycles:
                    if package in acycle:
                        allowed = True
                        break
                if not allowed:
                    cycled = [p.text for p in cycle.findall('package')]
                    comment.append(f"Package {package} appears in cycle {'/'.join(cycled)}")

        if len(comment):
            # New cycles, post comment.
            self.logger.info('cycle check: failed')
            return CheckResult(False, '\n'.join(comment) + '\n')

        self.logger.info('cycle check: passed')
        return CheckResult(True, None)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Do an installcheck on staging project')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project')
    parser.add_argument('--devel', type=str, default=None,
                        help='devel project (ex GNOME:Factory)')
    parser.add_argument('-r', '--repository', type=str, default=None,
                        help='repository to check, if not specified, use the staging configuration')
    parser.add_argument('-p', '--project', type=str, default='openSUSE:Factory',
                        help='project to check (ex. openSUSE:Factory, openSUSE:Leap:15.1)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']
    config = Config.get(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)
    if not args.repository:
        args.repository = api.cmain_repo
    staging_report = InstallChecker(api, config)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.staging:
        if not staging_report.staging(api.prj_from_short(args.staging), repository=args.repository, force=True):
            sys.exit(1)
    elif args.devel:
        if not staging_report.staging(args.devel, repository=args.repository, force=True, devel=True):
            sys.exit(1)
    else:
        for staging in api.get_staging_projects():
            if api.is_adi_project(staging):
                staging_report.staging(staging, repository=args.repository)
    sys.exit(0)
