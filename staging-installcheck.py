#!/usr/bin/python

from __future__ import print_function

import cmdln
from collections import namedtuple
import hashlib
from lxml import etree as ET
import os
import pipes
import re
import subprocess
import sys
import tempfile
import osc.core
import argparse
import logging

from osclib.cache_manager import CacheManager
from osc import conf
from osclib.conf import Config
from osclib.conf import str2bool
from osclib.core import BINARY_REGEX
from osclib.core import builddepinfo
from osclib.core import depends_on
from osclib.core import devel_project_fallback
from osclib.core import fileinfo_ext_all
from osclib.core import package_binary_list
from osclib.core import project_meta_revision
from osclib.core import project_pseudometa_file_ensure
from osclib.core import project_pseudometa_file_load
from osclib.core import project_pseudometa_package
from osclib.core import repository_path_search
from osclib.core import repository_path_expand
from osclib.core import repositories_states
from osclib.core import repository_arch_state
from osclib.core import repositories_published
from osclib.core import target_archs
from osclib.comments import CommentAPI
from osclib.memoize import memoize
from osclib.util import sha1_short
from osclib.stagingapi import StagingAPI

try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

import ReviewBot

CACHEDIR = CacheManager.directory('repository-meta')
SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))
INSTALL_REGEX = r"^(?:can't install (.*?)|found conflict of (.*?) with (.*?)):$"
InstallSection = namedtuple('InstallSection', ('binaries', 'text'))

ERROR_REPO_SPECIFIED = 'a repository must be specified via OSRT:Config main-repo for {}'

class InstallChecker(object):
    def __init__(self, api, config):
        self.api = api
        self.config = conf.config[api.project]
        self.logger = logging.getLogger('InstallChecker')
        self.commentapi = CommentAPI(api.apiurl)

        self.arch_whitelist = self.config.get('repo_checker-arch-whitelist')
        if self.arch_whitelist:
            self.arch_whitelist = set(self.arch_whitelist.split(' '))

        self.ring_whitelist = self.config.get('repo_checker-binary-whitelist-ring', '').split(' ')

        self.cycle_packages = self.config.get('repo_checker-allowed-in-cycles')
        self.calculate_allowed_cycles()

        self.existing_problems = self.binary_list_existing_problem(api.project, api.cmain_repo)

    def check_required_by(self, fileinfo, provides, requiredby, built_binaries, comments):
        if requiredby.get('name') in built_binaries:
            return True
        # extract >= and the like
        provide = provides.get('dep')
        provide = provide.split(' ')[0]
        comments.append('{} provides {} required by {}'.format(fileinfo.find('name').text, provide, requiredby.get('name')))
        url = api.makeurl(['build', api.project, api.cmain_repo, 'x86_64', '_repository', requiredby.get('name') + '.rpm'],
                      {'view': 'fileinfo_ext'})
        reverse_fileinfo = ET.parse(osc.core.http_GET(url)).getroot()
        for require in reverse_fileinfo.findall('requires_ext'):
            # extract >= and the like here too
            dep = require.get('dep').split(' ')[0]
            if dep != provide:
                continue
            for provided_by in require.findall('providedby'):
                if provided_by.get('name') in built_binaries:
                    continue
                comments.append('  also provided by {} -> ignoring'.format(provided_by.get('name')))
                return True
        comments.append('Error: missing alternative provides for {}'.format(provide))
        return False

    def check_delete_request(self, req, to_ignore, comments):
        package = req['package']
        if package in to_ignore:
            self.logger.info('Delete request for package {} ignored'.format(package))
            return True

        built_binaries = set([])
        file_infos = []
        for fileinfo in fileinfo_ext_all(self.api.apiurl, self.api.project, self.api.cmain_repo, 'x86_64', package):
            built_binaries.add(fileinfo.find('name').text)
            file_infos.append(fileinfo)

        result = True
        for fileinfo in file_infos:
            for provides in fileinfo.findall('provides_ext'):
                for requiredby in provides.findall('requiredby[@name]'):
                    result = result and self.check_required_by(fileinfo, provides, requiredby, built_binaries, comments)

        what_depends_on = depends_on(api.apiurl, api.project, api.cmain_repo, [package], True)

        # filter out dependency on package itself (happens with eg
        # java bootstrapping itself with previous build)
        if package in what_depends_on:
            what_depends_on.remove(package)

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
        return args

    def staging(self, project, force=False):
        api = self.api

        repository = self.api.cmain_repo

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
            pra = '{}/{}/{}'.format(project, repository, arch)
            buildid = self.buildid(project, repository, arch)
            if not buildid:
                self.logger.error('No build ID in {}'.format(pra))
                return False
            buildids[arch] = buildid
            url = self.report_url(project, repository, arch, buildid)
            try:
                root = ET.parse(osc.core.http_GET(url)).getroot()
                check = root.find('check[@name="installcheck"]/state')
                if check is not None and check.text != 'pending':
                    self.logger.info('{} already "{}", ignoring'.format(pra, check.text))
                else:
                    all_done = False
            except HTTPError:
                self.logger.info('{} has no status report'.format(pra))
                all_done = False

        if all_done and not force:
            return True

        repository_pairs = repository_path_expand(api.apiurl, project, repository)
        staging_pair = [project, repository]

        result = True

        status = api.project_status(project)
        if not status:
            self.logger.error('no project status for {}'.format(project))
            return False

        result_comment = []

        to_ignore = self.packages_to_ignore(project)
        meta = api.load_prj_pseudometa(status['description'])
        for req in meta['requests']:
            if req['type'] == 'delete':
                result = result and self.check_delete_request(req, to_ignore, result_comment)

        for arch in architectures:
            # hit the first repository in the target project (if existant)
            target_pair = None
            directories = []
            for pair_project, pair_repository in repository_pairs:
                # ignore repositories only inherited for config
                if repository_arch_state(self.api.apiurl, pair_project, pair_repository, arch):
                    if not target_pair and pair_project == api.project:
                        target_pair = [pair_project, pair_repository]

                    directories.append(self.mirror(pair_project, pair_repository, arch))

            if not api.is_adi_project(project):
                # For "leaky" ring packages in letter stagings, where the
                # repository setup does not include the target project, that are
                # not intended to to have all run-time dependencies satisfied.
                whitelist = self.ring_whitelist
            else:
                whitelist = self.existing_problems

            check = self.cycle_check(project, repository, arch)
            if not check.success:
                self.logger.warn('Cycle check failed')
                result_comment.append(check.comment + '\n')
                result = False

            check = self.install_check(target_pair, arch, directories, None, whitelist)
            if not check.success:
                self.logger.warn('Install check failed')
                result_comment.append(check.comment + '\n')
                result = False

        if result:
            self.report_state('success', self.gocd_url(), project, repository, buildids)
        else:
            result_comment.insert(0, 'Generated from {}\n\n'.format(self.gocd_url()))
            self.report_state('failure', self.upload_failure(project, result_comment), project, repository, buildids)
            self.logger.warn('Not accepting {}'.format(project))
            return False

        return result

    def upload_failure(self, project, comment):
        print(project, ''.join(comment))
        url = self.api.makeurl(['source', 'home:repo-checker', 'reports', project])
        osc.core.http_PUT(url, data=''.join(comment))

        return 'https://build.opensuse.org/package/view_file/home:repo-checker/reports/{}'.format(project)

    def report_state(self, state, report_url, project, repository, buildids):
        architectures = self.target_archs(project, repository)
        for arch in architectures:
            self.report_pipeline(state, report_url, project, repository, arch, buildids[arch], arch == architectures[-1])

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

    def report_pipeline(self, state, report_url, project, repository, architecture, buildid, is_last):
        url = self.report_url(project, repository, architecture, buildid)
        name = 'installcheck'
        # this is a little bit ugly, but we don't need 2 failures. So save a success for the
        # other archs to mark them as visited - pending we put in both
        if not is_last:
            if state == 'failure':
                state = 'success'

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

    @memoize(ttl=60, session=True, add_invalidate=True)
    def mirror(self, project, repository, arch):
        """Call bs_mirrorfull script to mirror packages."""
        directory = os.path.join(CACHEDIR, project, repository, arch)
        if not os.path.exists(directory):
            os.makedirs(directory)

        script = os.path.join(SCRIPT_PATH, 'bs_mirrorfull')
        path = '/'.join((project, repository, arch))
        url = '{}/public/build/{}'.format(self.api.apiurl, path)
        parts = ['LC_ALL=C', 'perl', script, '--nodebug', url, directory]
        parts = [pipes.quote(part) for part in parts]

        self.logger.info('mirroring {}'.format(path))
        if os.system(' '.join(parts)):
            raise Exception('failed to mirror {}'.format(path))

        return directory

    @memoize(session=True)
    def binary_list_existing_problem(self, project, repository):
        """Determine which binaries are mentioned in repo_checker output."""
        binaries = set()

        filename = self.project_pseudometa_file_name(project, repository)
        content = project_pseudometa_file_load(self.api.apiurl, project, filename)
        if not content:
            self.logger.warn('no project_only run from which to extract existing problems')
            return binaries

        sections = self.install_check_parse(content)
        for section in sections:
            for binary in section.binaries:
                match = re.match(BINARY_REGEX, binary)
                if match:
                    binaries.add(match.group('name'))

        return binaries

    def install_check(self, target_project_pair, arch, directories,
                      ignore=None, whitelist=[], parse=False, no_filter=False):
        self.logger.info('install check: start (ignore:{}, whitelist:{}, parse:{}, no_filter:{})'.format(
            bool(ignore), len(whitelist), parse, no_filter))

        with tempfile.NamedTemporaryFile() as ignore_file:
            # Print ignored rpms on separate lines in ignore file.
            if ignore:
                for item in ignore:
                    ignore_file.write(item + '\n')
                ignore_file.flush()

            # Invoke repo_checker.pl to perform an install check.
            script = os.path.join(SCRIPT_PATH, 'repo_checker.pl')
            parts = ['LC_ALL=C', 'perl', script, arch, ','.join(directories),
                     '-f', ignore_file.name, '-w', ','.join(whitelist)]
            if no_filter:
                parts.append('--no-filter')

            parts = [pipes.quote(part) for part in parts]
            p = subprocess.Popen(' '.join(parts), shell=True,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, close_fds=True)
            stdout, stderr = p.communicate()

        if p.returncode:
            self.logger.info('install check: failed')
            if p.returncode == 126:
                self.logger.warn('mirror cache reset due to corruption')
                self._invalidate_all()
            elif parse:
                # Parse output for later consumption for posting comments.
                sections = self.install_check_parse(stdout)
                self.install_check_sections_group(
                    target_project_pair[0], target_project_pair[1], arch, sections)

            # Format output as markdown comment.
            parts = []

            stdout = stdout.strip()
            if stdout:
                parts.append(stdout + '\n')
            stderr = stderr.strip()
            if stderr:
                parts.append(stderr + '\n')

            header = '### [install check & file conflicts for {}]'.format(arch)
            return CheckResult(False, header + '\n\n' + ('\n' + ('-' * 80) + '\n\n').join(parts))

        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def install_check_sections_group(self, project, repository, arch, sections):
        _, binary_map = package_binary_list(self.api.apiurl, project, repository, arch)

        for section in sections:
            # If switch to creating bugs likely makes sense to join packages to
            # form grouping key and create shared bugs for conflicts.
            # Added check for b in binary_map after encountering:
            # https://lists.opensuse.org/opensuse-buildservice/2017-08/msg00035.html
            # Under normal circumstances this should never occur.
            packages = set([binary_map[b] for b in section.binaries if b in binary_map])
            for package in packages:
                self.package_results.setdefault(package, [])
                self.package_results[package].append(section)

    def install_check_parse(self, output):
        section = None
        text = None

        # Loop over lines and parse into chunks assigned to binaries.
        for line in output.splitlines(True):
            if line.startswith(' '):
                if section:
                    text += line
            else:
                if section:
                    yield InstallSection(section, text)

                match = re.match(INSTALL_REGEX, line)
                if match:
                    # Remove empty groups since regex matches different patterns.
                    binaries = [b for b in match.groups() if b is not None]
                    section = binaries
                    text = line
                else:
                    section = None

        if section:
            yield InstallSection(section, text)

    def calculate_allowed_cycles(self):
        self.allowed_cycles = []
        if self.cycle_packages:
            for comma_list in self.cycle_packages.split(';'):
                self.allowed_cycles.append(comma_list.split(','))

    def cycle_check(self, project, repository, arch):
        self.logger.info('cycle check: start %s/%s/%s' % (project, repository, arch))
        comment = []

        depinfo = builddepinfo(self.api.apiurl, project, repository, arch, order = False)
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
                    comment.append('Package {} appears in cycle {}'.format(package, '/'.join(cycled)))

        if len(comment):
            # New cycles, post comment.
            self.logger.info('cycle check: failed')
            return CheckResult(False, '\n'.join(comment) + '\n')

        self.logger.info('cycle check: passed')
        return CheckResult(True, None)

    def project_pseudometa_file_name(self, project, repository):
        filename = 'repo_checker'

        main_repo = Config.get(self.api.apiurl, project).get('main-repo')
        if not main_repo:
            filename += '.' + repository

        return filename

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Do an installcheck on staging project')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project')
    parser.add_argument('-p', '--project', type=str, default='openSUSE:Factory',
                        help='project to check (ex. openSUSE:Factory, openSUSE:Leap:15.1)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']
    config = Config(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)
    staging_report = InstallChecker(api, config)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    result = True
    if args.staging:
        result = staging_report.staging(api.prj_from_short(args.staging), force=True)
    else:
        for staging in api.get_staging_projects():
            if api.is_adi_project(staging):
                result = staging_report.staging(staging) and result

    if not result:
        sys.exit( 1 )
