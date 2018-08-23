#!/usr/bin/python

from __future__ import print_function

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

from osclib.conf import Config
from osclib.conf import str2bool
from osclib.core import BINARY_REGEX
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
from osclib.core import repositories_published
from osclib.core import target_archs
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR
from osclib.memoize import memoize
from osclib.util import sha1_short

import ReviewBot

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))
INSTALL_REGEX = r"^(?:can't install (.*?)|found conflict of (.*?) with (.*?)):$"
InstallSection = namedtuple('InstallSection', ('binaries', 'text'))

ERROR_REPO_SPECIFIED = 'a repository must be specified via OSRT:Config main-repo for {}'

class RepoChecker(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.request_default_return = True
        self.comment_handler = True

        # RepoChecker options.
        self.skip_cycle = False
        self.force = False

    def project_only(self, project, post_comments=False):
        repository = self.project_repository(project)
        if not repository:
            self.logger.error(ERROR_REPO_SPECIFIED.format(project))
            return

        repository_pairs = repository_path_expand(self.apiurl, project, repository)
        state_hash = self.repository_state(repository_pairs)
        self.repository_check(repository_pairs, state_hash, False, bool(post_comments))

    def package_comments(self, project):
        self.logger.info('{} package comments'.format(len(self.package_results)))

        for package, sections in self.package_results.items():
            if str2bool(Config.get(self.apiurl, project).get('repo_checker-package-comment-devel', 'False')):
                bot_name_suffix = project
                comment_project, comment_package = devel_project_fallback(self.apiurl, project, package)
                if comment_project is None or comment_package is None:
                    self.logger.warning('unable to find devel project for {}'.format(package))
                    continue

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

    def target_archs(self, project, repository):
        archs = target_archs(self.apiurl, project, repository)

        # Check for arch whitelist and use intersection.
        whitelist = Config.get(self.apiurl, project).get('repo_checker-arch-whitelist')
        if whitelist:
            archs = list(set(whitelist.split(' ')).intersection(set(archs)))

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
        url = '{}/public/build/{}'.format(self.apiurl, path)
        parts = ['LC_ALL=C', 'perl', script, '--nodebug', url, directory]
        parts = [pipes.quote(part) for part in parts]

        self.logger.info('mirroring {}'.format(path))
        if os.system(' '.join(parts)):
            raise Exception('failed to mirror {}'.format(path))

        return directory

    def simulated_merge_ignore(self, override_pair, overridden_pair, arch):
        """Determine the list of binaries to similate overides in overridden layer."""
        _, binary_map = package_binary_list(self.apiurl, override_pair[0], override_pair[1], arch)
        packages = set(binary_map.values())

        binaries, _ = package_binary_list(self.apiurl, overridden_pair[0], overridden_pair[1], arch)
        for binary in binaries:
            if binary.package in packages:
                yield binary.name

    @memoize(session=True)
    def binary_list_existing_problem(self, project, repository):
        """Determine which binaries are mentioned in repo_checker output."""
        binaries = set()

        filename = self.project_pseudometa_file_name(project, repository)
        content = project_pseudometa_file_load(self.apiurl, project, filename)
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

    def binary_whitelist(self, override_pair, overridden_pair, arch):
        whitelist = self.binary_list_existing_problem(overridden_pair[0], overridden_pair[1])

        if Config.get(self.apiurl, overridden_pair[0]).get('staging'):
            additions = self.staging_api(overridden_pair[0]).get_prj_pseudometa(
                override_pair[0]).get('config', {})
            prefix = 'repo_checker-binary-whitelist'
            for key in [prefix, '-'.join([prefix, arch])]:
                whitelist.update(additions.get(key, '').split(' '))

        whitelist = filter(None, whitelist)
        return whitelist

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
                parts.append('<pre>\n' + stdout + '\n' + '</pre>\n')
            stderr = stderr.strip()
            if stderr:
                parts.append('<pre>\n' + stderr + '\n' + '</pre>\n')

            pseudometa_project, pseudometa_package = project_pseudometa_package(
                self.apiurl, target_project_pair[0])
            filename = self.project_pseudometa_file_name(target_project_pair[0], target_project_pair[1])
            path = ['package', 'view_file', pseudometa_project, pseudometa_package, filename]
            header = '### [install check & file conflicts](/{})\n\n'.format('/'.join(path))
            return CheckResult(False, header + ('\n' + ('-' * 80) + '\n\n').join(parts))

        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def install_check_sections_group(self, project, repository, arch, sections):
        _, binary_map = package_binary_list(self.apiurl, project, repository, arch)

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

    @memoize(ttl=60, session=True)
    def cycle_check_skip(self, project):
        if self.skip_cycle:
            return True

        # Look for skip-cycle comment command.
        comments = self.comment_api.get_comments(project_name=project)
        users = self.request_override_check_users(project)
        for _, who in self.comment_api.command_find(
            comments, self.review_user, 'skip-cycle', users):
            self.logger.debug('comment command: skip-cycle by {}'.format(who))
            return True

        return False

    def cycle_check(self, override_pair, overridden_pair, arch):
        if self.cycle_check_skip(override_pair[0]):
            self.logger.info('cycle check: skip due to --skip-cycle or comment command')
            return CheckResult(True, None)

        self.logger.info('cycle check: start')
        comment = []
        first = True
        cycle_detector = CycleDetector(self.staging_api(overridden_pair[0]))
        for index, (cycle, new_edges, new_packages) in enumerate(
            cycle_detector.cycles(override_pair, overridden_pair, arch), start=1):

            if not new_packages:
                continue

            if first:
                comment.append('### new [cycle(s)](/project/repository_state/{}/{})\n'.format(
                    override_pair[0], override_pair[1]))
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

    def result_comment(self, repository, arch, results, comment):
        """Generate comment from results"""
        comment.append('## {}/{}\n'.format(repository, arch))
        for result in results.values():
            if not result.success:
                comment.append(result.comment)

    def project_pseudometa_file_name(self, project, repository):
        filename = 'repo_checker'

        main_repo = Config.get(self.apiurl, project).get('main-repo')
        if not main_repo:
            filename += '.' + repository

        return filename

    @memoize(ttl=60, session=True)
    def repository_state(self, repository_pairs):
        states = repositories_states(self.apiurl, repository_pairs)
        states.append(str(project_meta_revision(self.apiurl, repository_pairs[0][0])))

        return sha1_short(states)

    @memoize(ttl=60, session=True)
    def repository_state_last(self, project, repository, pseudometa):
        if pseudometa:
            filename = self.project_pseudometa_file_name(project, repository)
            content = project_pseudometa_file_load(self.apiurl, project, filename)
            if content:
                return content.splitlines()[0]
        else:
            comments = self.comment_api.get_comments(project_name=project)
            _, info = self.comment_api.comment_find(comments, self.bot_name)
            if info:
                return info.get('state')

        return None

    @memoize(session=True)
    def repository_check(self, repository_pairs, state_hash, simulate_merge, post_comments=False):
        comment = []
        project, repository = repository_pairs[0]
        self.logger.info('checking {}/{}@{}[{}]'.format(
            project, repository, state_hash, len(repository_pairs)))

        published = repositories_published(self.apiurl, repository_pairs)

        if not self.force:
            if state_hash == self.repository_state_last(project, repository, not simulate_merge):
                self.logger.info('{} build unchanged'.format(project))
                # TODO keep track of skipped count for cycle summary
                return None

            # For submit style requests, want to process if top layer is done,
            # but not mark review as final until all layers are published.
            if published is not True and (not simulate_merge or published[0] == project):
                # Require all layers to be published except when the top layer
                # is published in a simulate merge (allows quicker feedback with
                # potentially incorrect resutls for staging).
                self.logger.info('{}/{} not published'.format(published[0], published[1]))
                return None

        # Drop non-published repository information and thus reduce to boolean.
        published = published is True

        if simulate_merge:
            # Restrict top layer archs to the whitelisted archs from merge layer.
            archs = set(target_archs(self.apiurl, project, repository)).intersection(
                    set(self.target_archs(repository_pairs[1][0], repository_pairs[1][1])))
        else:
            # Top of pseudometa file.
            comment.append(state_hash)
            archs = self.target_archs(project, repository)

            if post_comments:
                # Stores parsed install_check() results grouped by package.
                self.package_results = {}

        if not len(archs):
            self.logger.debug('{} has no relevant architectures'.format(project))
            return None

        result = True
        for arch in archs:
            directories = []
            for pair_project, pair_repository in repository_pairs:
                directories.append(self.mirror(pair_project, pair_repository, arch))

            if simulate_merge:
                ignore = self.simulated_merge_ignore(repository_pairs[0], repository_pairs[1], arch)
                whitelist = self.binary_whitelist(repository_pairs[0], repository_pairs[1], arch)

                results = {
                    'cycle': self.cycle_check(repository_pairs[0], repository_pairs[1], arch),
                    'install': self.install_check(
                        repository_pairs[1], arch, directories, ignore, whitelist),
                }
            else:
                # Only products themselves will want no-filter or perhaps
                # projects working on cleaning up a product.
                no_filter = str2bool(Config.get(self.apiurl, project).get('repo_checker-no-filter'))
                results = {
                    'cycle': CheckResult(True, None),
                    'install': self.install_check(repository_pairs[0], arch, directories,
                                                  parse=post_comments, no_filter=no_filter),
                }

            if not all(result.success for _, result in results.items()):
                # Not all checks passed, build comment.
                result = False
                self.result_comment(repository, arch, results, comment)

        if simulate_merge:
            info_extra = {'state': state_hash}
            if not result:
                # Some checks in group did not pass, post comment.
                # Avoid identical comments with different build hash during
                # target project build phase. Once published update regardless.
                self.comment_write(state='seen', result='failed', project=project,
                                   message='\n'.join(comment).strip(), identical=True,
                                   info_extra=info_extra, info_extra_identical=published)
            else:
                # Post passed comment only if previous failed comment.
                text = 'Previously reported problems have been resolved.'
                self.comment_write(state='done', result='passed', project=project,
                                   message=text, identical=True, only_replace=True,
                                   info_extra=info_extra)
        else:
            text = '\n'.join(comment).strip()
            if not self.dryrun:
                filename = self.project_pseudometa_file_name(project, repository)
                project_pseudometa_file_ensure(
                    self.apiurl, project, filename, text + '\n', 'repo_checker project_only run')
            else:
                print(text)

            if post_comments:
                self.package_comments(project)

        if result and not published:
            # Wait for the complete stack to build before positive result.
            self.logger.debug('demoting result from accept to ignore due to non-published layer')
            result = None

        return result

    @memoize(session=True)
    def project_repository(self, project):
        repository = Config.get(self.apiurl, project).get('main-repo')
        if not repository:
            self.logger.debug('no main-repo defined for {}'.format(project))

            search_project = 'openSUSE:Factory'
            for search_repository in ('snapshot', 'standard'):
                repository = repository_path_search(
                    self.apiurl, project, search_project, search_repository)

                if repository:
                    self.logger.debug('found chain to {}/{} via {}'.format(
                        search_project, search_repository, repository))
                    break

        return repository

    @memoize(ttl=60, session=True)
    def request_repository_pairs(self, request, action):
        if str2bool(Config.get(self.apiurl, action.tgt_project).get('repo_checker-project-skip', 'False')):
            # Do not change message as this should only occur in requests
            # targeting multiple projects such as in maintenance workflow in
            # which the message should be set by other actions.
            self.logger.debug('skipping review of action targeting {}'.format(action.tgt_project))
            return True

        repository = self.project_repository(action.tgt_project)
        if not repository:
            self.review_messages['declined'] = ERROR_REPO_SPECIFIED.format(action.tgt_project)
            return False

        repository_pairs = []
        # Assumes maintenance_release target project has staging disabled.
        if Config.get(self.apiurl, action.tgt_project).get('staging'):
            stage_info = self.staging_api(action.tgt_project).packages_staged.get(action.tgt_package)
            if not stage_info or str(stage_info['rq_id']) != str(request.reqid):
                self.logger.info('{} not staged'.format(request.reqid))
                return None

            # Staging setup is convoluted and thus the repository setup does not
            # contain a path to the target project. Instead the ports repository
            # is used to import the target prjconf. As such the staging group
            # repository must be explicitly layered on top of target project.
            repository_pairs.append([stage_info['prj'], repository])
            repository_pairs.extend(repository_path_expand(self.apiurl, action.tgt_project, repository))
        else:
            # Find a repository which links to target project "main" repository.
            repository = repository_path_search(
                self.apiurl, action.src_project, action.tgt_project, repository)
            if not repository:
                self.review_messages['declined'] = ERROR_REPO_SPECIFIED.format(action.tgt_project)
                return False

            repository_pairs.extend(repository_path_expand(self.apiurl, action.src_project, repository))

        return repository_pairs

    def check_action_submit(self, request, action):
        repository_pairs = self.request_repository_pairs(request, action)
        if not isinstance(repository_pairs, list):
            return repository_pairs

        state_hash = self.repository_state(repository_pairs)
        if not self.repository_check(repository_pairs, state_hash, True):
            return None

        self.review_messages['accepted'] = 'cycle and install check passed'
        return True

    def check_action_delete(self, request, action):
        # TODO Ignore tgt_project packages that depend on this that are part of
        # ignore list as and instead look at output from staging for those.

        built_binaries = set([])
        revdeps = set([])
        for fileinfo in fileinfo_ext_all(self.apiurl, action.tgt_project, 'standard', 'x86_64', action.tgt_package):
            built_binaries.add(fileinfo.find('name').text)
            for requiredby in fileinfo.findall('provides_ext/requiredby[@name]'):
                revdeps.add(requiredby.get('name'))
        runtime_deps = sorted(revdeps - built_binaries)

        what_depends_on = depends_on(self.apiurl, action.tgt_project, 'standard', [action.tgt_package], True)

        # filter out dependency on package itself (happens with eg
        # java bootstrapping itself with previous build)
        if action.tgt_package in what_depends_on:
            what_depends_on.remove(action.tgt_package)

        if len(what_depends_on):
            self.logger.warn('{} is still a build requirement of:\n\n- {}'.format(
                action.tgt_package, '\n- '.join(sorted(what_depends_on))))

        if len(runtime_deps):
            self.logger.warn('{} provides runtime dependencies to:\n\n- {}'.format(
                action.tgt_package, '\n- '.join(runtime_deps)))

        if len(self.comment_handler.lines):
            self.comment_write(state='seen', result='failed')
            return None

        repository_pairs = self.request_repository_pairs(request, action)
        if not isinstance(repository_pairs, list):
            return repository_pairs

        state_hash = self.repository_state(repository_pairs)
        if not self.repository_check(repository_pairs, state_hash, True):
            return None

        self.review_messages['accepted'] = 'cycle and install check passed'
        return True

    def check_action_maintenance_release(self, request, action):
        # No reason to special case patchinfo since same source and target
        # projects which is all that repo_checker cares about.

        repository_pairs = self.request_repository_pairs(request, action)
        if not isinstance(repository_pairs, list):
            return repository_pairs

        state_hash = self.repository_state(repository_pairs)
        if not self.repository_check(repository_pairs, state_hash, True):
            return None

        self.review_messages['accepted'] = 'cycle and install check passed'
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = RepoChecker

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('--skip-cycle', action='store_true', help='skip cycle check')
        parser.add_option('--force', action='store_true', help='force review even if project is not ready')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.skip_cycle:
            bot.skip_cycle = self.options.skip_cycle

        bot.force = self.options.force

        return bot

    @cmdln.option('--post-comments', action='store_true', help='post comments to packages with issues')
    def do_project_only(self, subcmd, opts, project):
        self.checker.check_requests() # Needed to properly init ReviewBot.
        self.checker.project_only(project, opts.post_comments)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
