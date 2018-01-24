#!/usr/bin/python

import cmdln
from collections import namedtuple
import hashlib
from lxml import etree as ET
import os
from osc.core import show_results_meta
import pipes
import re
import subprocess
import sys
import tempfile

from osclib.comments import CommentAPI
from osclib.core import binary_list
from osclib.core import depends_on
from osclib.core import devel_project_fallback
from osclib.core import package_binary_list
from osclib.core import request_staged
from osclib.core import target_archs
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR

import ReviewBot

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))
INSTALL_REGEX = r"^(?:can't install (.*?)|found conflict of (.*?) with (.*?)):$"
InstallSection = namedtuple('InstallSection', ('binaries', 'text'))

class RepoChecker(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.only_one_action = True
        self.request_default_return = True
        self.comment_handler = True

        # RepoChecker options.
        self.skip_cycle = False
        self.force = False
        self.limit_group = None

    def repository_published(self, project):
        root = ET.fromstringlist(show_results_meta(
            self.apiurl, project, multibuild=True, repository=['standard']))
        return not len(root.xpath('result[@state!="published"]'))

    def project_only(self, project, post_comments=False):
        # self.staging_config needed by target_archs().
        api = self.staging_api(project)

        if not self.force and not self.repository_published(project):
            self.logger.info('{}/standard not published'.format(project))
            return

        comment = []
        for arch in self.target_archs(project):
            directory_project = self.mirror(project, arch)

            parse = project if post_comments else False
            results = {
                'cycle': CheckResult(True, None),
                'install': self.install_check(project, [directory_project], arch, parse=parse),
            }

            if not all(result.success for _, result in results.items()):
                self.result_comment(arch, results, comment)

        text = '\n'.join(comment).strip()
        if not self.dryrun:
            api.dashboard_content_ensure('repo_checker', text, 'project_only run')
        else:
            print(text)

        if post_comments:
            self.package_comments(project)

    def package_comments(self, project):
        self.logger.info('{} package comments'.format(len(self.package_results)))

        for package, sections in self.package_results.items():
            if bool(self.staging_config[project].get('repo_checker-package-comment-devel', True)):
                bot_name_suffix = project
                comment_project, comment_package = devel_project_fallback(self.apiurl, project, package)
                message = 'The version of this package in [`{project}`](/package/show/{project}/{package}) ' \
                    'has installation issues and may not be installable:'.format(
                        project=project, package=package)
            else:
                bot_name_suffix = None
                comment_project = project
                comment_package = package
                message = 'This package has installation issues and may not be installable:'

            # Sort sections by text to group binaries together.
            sections = sorted(sections, key=lambda s: s.text)
            message += '\n\n<pre>\n{}\n</pre>'.format(
                '\n'.join([section.text for section in sections]).strip())

            # Generate a hash based on the binaries involved and the number of
            # sections. This eliminates version or release changes from causing
            # an update to the comment while still updating on relevant changes.
            binaries = set()
            for section in sections:
                binaries.update(section.binaries)
            info = ';'.join(['::'.join(sorted(binaries)), str(len(sections))])
            reference = hashlib.sha1(info).hexdigest()[:7]

            # Post comment on package in order to notifiy maintainers.
            self.comment_write(state='seen', result=reference, bot_name_suffix=bot_name_suffix,
                               project=comment_project, package=comment_package, message=message)

    def prepare_review(self):
        # Reset for request batch.
        self.requests_map = {}
        self.groups = {}
        self.groups_build = {}

        # Manipulated in ensure_group().
        self.group = None
        self.mirrored = set()

        # Stores parsed install_check() results grouped by package.
        self.package_results = {}

        # Look for requests of interest and group by staging.
        skip_build = set()
        for request in self.requests:
            # Only interesting if request is staged.
            group = request_staged(request)
            if not group:
                self.logger.debug('{}: not staged'.format(request.reqid))
                continue

            if self.limit_group and group != self.limit_group:
                continue

            # Only interested if group has completed building.
            api = self.staging_api(request.actions[0].tgt_project)
            status = api.project_status(group, True)
            # Corrupted requests may reference non-existent projects and will
            # thus return a None status which should be considered not ready.
            if not status or str(status['overall_state']) not in ('testing', 'review', 'acceptable'):
                # Not in a "ready" state.
                openQA_only = False # Not relevant so set to False.
                if status and str(status['overall_state']) == 'failed':
                    # Exception to the rule is openQA only in failed state.
                    openQA_only = True
                    for project in api.project_status_walk(status):
                        if len(project['broken_packages']):
                            # Broken packages so not just openQA.
                            openQA_only = False
                            break

                if not self.force and not openQA_only:
                    self.logger.debug('{}: {} not ready'.format(request.reqid, group))
                    continue

            # Only interested if request is in consistent state.
            selected = api.project_status_requests('selected')
            if request.reqid not in selected:
                self.logger.debug('{}: inconsistent state'.format(request.reqid))

            if group not in self.groups_build:
                # Generate build hash based on hashes from relevant projects.
                builds = []
                for staging in api.staging_walk(group):
                    builds.append(ET.fromstringlist(show_results_meta(
                        self.apiurl, staging, multibuild=True, repository=['standard'])).get('state'))
                builds.append(ET.fromstringlist(show_results_meta(
                    self.apiurl, api.project, multibuild=True, repository=['standard'])).get('state'))

                # Include meta revision for config changes (like whitelist).
                builds.append(str(api.get_prj_meta_revision(group)))
                self.groups_build[group] = hashlib.sha1(''.join(builds)).hexdigest()[:7]

                # Determine if build has changed since last comment.
                comment_api = CommentAPI(api.apiurl)
                comments = comment_api.get_comments(project_name=group)
                _, info = comment_api.comment_find(comments, self.bot_name)
                if info and self.groups_build[group] == info.get('build'):
                    skip_build.add(group)

            if not self.force and group in skip_build:
                self.logger.debug('{}: {} build unchanged'.format(request.reqid, group))
                continue

            self.requests_map[int(request.reqid)] = group

            requests = self.groups.get(group, [])
            requests.append(request)
            self.groups[group] = requests

            self.logger.debug('{}: {} ready'.format(request.reqid, group))

        # Filter out undesirable requests and ensure requests are ordered
        # together with group for efficiency.
        count_before = len(self.requests)
        self.requests = []
        for group, requests in sorted(self.groups.items()):
            self.requests.extend(requests)

        self.logger.debug('requests: {} skipped, {} queued'.format(
            count_before - len(self.requests), len(self.requests)))

    def ensure_group(self, request, action):
        project = action.tgt_project
        group = self.requests_map[int(request.reqid)]

        if group == self.group:
            # Only process a group the first time it is encountered.
            return self.group_pass

        self.logger.info('group {}'.format(group))
        self.group = group
        self.group_pass = True

        comment = []
        for arch in self.target_archs(project):
            stagings = []
            directories = []
            ignore = set()

            for staging in self.staging_api(project).staging_walk(group):
                if arch not in self.target_archs(staging):
                    self.logger.debug('{}/{} not available'.format(staging, arch))
                    continue

                stagings.append(staging)
                directories.append(self.mirror(staging, arch))
                ignore.update(self.ignore_from_staging(project, staging, arch))

            if not len(stagings):
                continue

            # Only bother if staging can match arch, but layered first.
            directories.insert(0, self.mirror(project, arch))

            whitelist = self.binary_whitelist(project, arch, group)

            # Perform checks on group.
            results = {
                'cycle': self.cycle_check(project, stagings, arch),
                'install': self.install_check(project, directories, arch, ignore, whitelist),
            }

            if not all(result.success for _, result in results.items()):
                # Not all checks passed, build comment.
                self.group_pass = False
                self.result_comment(arch, results, comment)

        info_extra = {'build': self.groups_build[group]}
        if not self.group_pass:
            # Some checks in group did not pass, post comment.
            # Avoid identical comments with different build hash during target
            # project build phase. Once published update regardless.
            published = self.repository_published(project)
            self.comment_write(state='seen', result='failed', project=group,
                               message='\n'.join(comment).strip(), identical=True,
                               info_extra=info_extra, info_extra_identical=published)
        else:
            # Post passed comment only if previous failed comment.
            text = 'Previously reported problems have been resolved.'
            self.comment_write(state='done', result='passed', project=group,
                               message=text, identical=True, only_replace=True,
                               info_extra=info_extra)

        return self.group_pass

    def target_archs(self, project):
        archs = target_archs(self.apiurl, project)

        # Check for arch whitelist and use intersection.
        product = project.split(':Staging:', 1)[0]
        whitelist = self.staging_config[product].get('repo_checker-arch-whitelist')
        if whitelist:
            archs = list(set(whitelist.split(' ')).intersection(set(archs)))

        # Trick to prioritize x86_64.
        return sorted(archs, reverse=True)

    def mirror(self, project, arch):
        """Call bs_mirrorfull script to mirror packages."""
        directory = os.path.join(CACHEDIR, project, 'standard', arch)
        if (project, arch) in self.mirrored:
            # Only mirror once per request batch.
            return directory

        if not os.path.exists(directory):
            os.makedirs(directory)

        script = os.path.join(SCRIPT_PATH, 'bs_mirrorfull')
        path = '/'.join((project, 'standard', arch))
        url = '{}/public/build/{}'.format(self.apiurl, path)
        parts = ['LC_ALL=C', 'perl', script, '--nodebug', url, directory]
        parts = [pipes.quote(part) for part in parts]

        self.logger.info('mirroring {}'.format(path))
        if os.system(' '.join(parts)):
            raise Exception('failed to mirror {}'.format(path))

        self.mirrored.add((project, arch))
        return directory

    def ignore_from_staging(self, project, staging, arch):
        """Determine the target project binaries to ingore in favor of staging."""
        _, binary_map = package_binary_list(self.apiurl, staging, 'standard', arch)
        packages = set(binary_map.values())

        binaries, _ = package_binary_list(self.apiurl, project, 'standard', arch)
        for binary in binaries:
            if binary.package in packages:
                yield binary.name

    def binary_whitelist(self, project, arch, group):
        additions = self.staging_api(project).get_prj_pseudometa(group).get('config', {})
        prefix = 'repo_checker-binary-whitelist'
        whitelist = set()
        for key in [prefix, '-'.join([prefix, arch])]:
            whitelist.update(self.staging_config[project].get(key, '').split(' '))
            whitelist.update(additions.get(key, '').split(' '))
        whitelist = filter(None, whitelist)
        return whitelist

    def install_check(self, project, directories, arch, ignore=[], whitelist=[], parse=False):
        self.logger.info('install check: start')

        with tempfile.NamedTemporaryFile() as ignore_file:
            # Print ignored rpms on separate lines in ignore file.
            for item in ignore:
                ignore_file.write(item + '\n')
            ignore_file.flush()

            directory_project = directories.pop(0) if len(directories) > 1 else None

            # Invoke repo_checker.pl to perform an install check.
            script = os.path.join(SCRIPT_PATH, 'repo_checker.pl')
            parts = ['LC_ALL=C', 'perl', script, arch, ','.join(directories),
                     '-f', ignore_file.name, '-w', ','.join(whitelist)]
            if directory_project:
                parts.extend(['-r', directory_project])

            parts = [pipes.quote(part) for part in parts]
            p = subprocess.Popen(' '.join(parts), shell=True,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, close_fds=True)
            stdout, stderr = p.communicate()

        if p.returncode:
            self.logger.info('install check: failed')
            if p.returncode == 126:
                self.logger.warn('mirror cache reset due to corruption')
                self.mirrored = set()
            elif parse:
                # Parse output for later consumption for posting comments.
                sections = self.install_check_parse(stdout)
                self.install_check_sections_group(parse, arch, sections)

            # Format output as markdown comment.
            parts = []

            stdout = stdout.strip()
            if stdout:
                parts.append('<pre>\n' + stdout + '\n' + '</pre>\n')
            stderr = stderr.strip()
            if stderr:
                parts.append('<pre>\n' + stderr + '\n' + '</pre>\n')

            header = '### [install check & file conflicts](/package/view_file/{}:Staging/dashboard/repo_checker)\n\n'.format(project)
            return CheckResult(False, header + ('\n' + ('-' * 80) + '\n\n').join(parts))


        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def install_check_sections_group(self, project, arch, sections):
        _, binary_map = package_binary_list(self.apiurl, project, 'standard', arch)

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

    def cycle_check(self, project, stagings, arch):
        if self.skip_cycle:
            self.logger.info('cycle check: skip due to --skip-cycle')
            return CheckResult(True, None)

        self.logger.info('cycle check: start')
        cycle_detector = CycleDetector(self.staging_api(project))
        comment = []
        for staging in stagings:
            first = True
            for index, (cycle, new_edges, new_packages) in enumerate(
                cycle_detector.cycles(staging, arch=arch), start=1):
                if not new_packages:
                    continue

                if first:
                    comment.append('### new [cycle(s)](/project/repository_state/{}/standard)\n'.format(staging))
                    first = False

                # New package involved in cycle, build comment.
                comment.append('- #{}: {} package cycle, {} new edges'.format(
                    index, len(cycle), len(new_edges)))

                comment.append('   - cycle')
                for package in sorted(cycle):
                    comment.append('      - {}'.format(package))

                comment.append('   - new edges')
                for edge in sorted(new_edges):
                    comment.append('      - ({}, {})'.format(edge[0], edge[1]))

        if len(comment):
            # New cycles, post comment.
            self.logger.info('cycle check: failed')
            return CheckResult(False, '\n'.join(comment) + '\n')

        self.logger.info('cycle check: passed')
        return CheckResult(True, None)

    def result_comment(self, arch, results, comment):
        """Generate comment from results"""
        comment.append('## ' + arch + '\n')
        for result in results.values():
            if not result.success:
                comment.append(result.comment)

    def check_action_submit(self, request, action):
        if not self.ensure_group(request, action):
            return None

        self.review_messages['accepted'] = 'cycle and install check passed'
        return True

    def check_action_delete(self, request, action):
        # TODO Include runtime dependencies instead of just build dependencies.
        # TODO Ignore tgt_project packages that depend on this that are part of
        # ignore list as and instead look at output from staging for those.
        what_depends_on = depends_on(self.apiurl, action.tgt_project, 'standard', [action.tgt_package], True)

        # filter out dependency on package itself (happens with eg
        # java bootstrapping itself with previous build)
        if action.tgt_package in what_depends_on:
            what_depends_on.remove(action.tgt_package)

        if len(what_depends_on):
            self.logger.warn('{} is still a build requirement of {}'.format(action.tgt_package, ', '.join(what_depends_on)))

        if len(self.comment_handler.lines):
            self.comment_write(state='seen', result='failed')
            return None

        # Allow for delete to be declined before ensuring group passed.
        if not self.ensure_group(request, action):
            return None

        self.review_messages['accepted'] = 'delete request is safe'
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = RepoChecker

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('--skip-cycle', action='store_true', help='skip cycle check')
        parser.add_option('--force', action='store_true', help='force review even if project is not ready')
        parser.add_option('--limit-group', metavar='GROUP', help='only review requests in specific group')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.skip_cycle:
            bot.skip_cycle = self.options.skip_cycle

        bot.force = self.options.force
        bot.limit_group = self.options.limit_group

        return bot

    @cmdln.option('--post-comments', action='store_true', help='post comments to packages with issues')
    def do_project_only(self, subcmd, opts, project):
        self.checker.check_requests() # Needed to properly init ReviewBot.
        self.checker.project_only(project, opts.post_comments)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
