#!/usr/bin/python

from collections import namedtuple
import os
import pipes
import subprocess
import sys
import tempfile

from osc import conf
from osclib.conf import Config
from osclib.core import binary_list
from osclib.core import depends_on
from osclib.core import request_staged
from osclib.core import target_archs
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR
from osclib.stagingapi import StagingAPI

import ReviewBot

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CheckResult = namedtuple('CheckResult', ('success', 'comment'))

class RepoChecker(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.only_one_action = True
        self.request_default_return = True
        self.comment_handler = True

        # RepoChecker options.
        self.skip_cycle = False

    def staging_api(self, project):
        if project not in self.staging_apis:
            config = Config(project)
            self.staging_apis[project] = StagingAPI(self.apiurl, project)

            config.apply_remote(self.staging_apis[project])
            self.staging_config[project] = conf.config[project].copy()

        return self.staging_apis[project]

    def prepare_review(self):
        # Reset for request batch.
        self.staging_apis = {}
        self.staging_config = {}
        self.requests_map = {}
        self.groups = {}

        # Manipulated in ensure_group().
        self.group = None
        self.mirrored = set()

        # Look for requests of interest and group by staging.
        for request in self.requests:
            # Only interesting if request is staged.
            group = request_staged(request)
            if not group:
                self.logger.debug('{}: not staged'.format(request.reqid))
                continue

            # Only interested if group has completed building.
            api = self.staging_api(request.actions[0].tgt_project)
            status = api.project_status(group, True)
            # Corrupted requests may reference non-existent projects and will
            # thus return a None status which should be considered not ready.
            if not status or str(status['overall_state']) not in ('testing', 'review', 'acceptable'):
                self.logger.debug('{}: {} not ready'.format(request.reqid, group))
                continue

            # Only interested if request is in consistent state.
            selected = api.project_status_requests('selected')
            if request.reqid not in selected:
                self.logger.debug('{}: inconsistent state'.format(request.reqid))

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
            if arch not in self.target_archs(group):
                self.logger.debug('{}/{} not available'.format(group, arch))
                continue

            # Mirror both projects the first time each are encountered.
            directory_project = self.mirror(project, arch)
            directory_group = self.mirror(group, arch)

            # Generate list of rpms to ignore from the project consisting of all
            # packages in group and those that were deleted.
            ignore = set()
            self.ignore_from_repo(directory_group, ignore)

            for r in self.groups[group]:
                a = r.actions[0]
                if a.type == 'delete':
                    self.ignore_from_package(project, a.tgt_package, arch, ignore)

            # Perform checks on group.
            results = {
                'cycle': self.cycle_check(project, group, arch),
                'install': self.install_check(directory_project, directory_group, arch, ignore),
            }

            if not all(result.success for _, result in results.items()):
                # Not all checks passed, build comment.
                self.group_pass = False
                self.result_comment(project, group, arch, results, comment)

        if not self.group_pass:
            # Some checks in group did not pass, post comment.
            self.comment_write(state='seen', result='failed', project=group,
                               message='\n'.join(comment).strip(), identical=True)

        return self.group_pass

    def target_archs(self, project):
        archs = target_archs(self.apiurl, project)

        # Check for arch whitelist and use intersection.
        product = project.split(':Staging:', 1)[0]
        whitelist = self.staging_config[product].get('repo_checker-arch-whitelist')
        if whitelist:
            archs = list(set(whitelist.split(' ')).intersection(set(archs)))

        # Trick to prioritize x86_64.
        return reversed(archs)

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

    def ignore_from_repo(self, directory, ignore):
        """Extract rpm names from mirrored repo directory."""
        for filename in os.listdir(directory):
            if not filename.endswith('.rpm'):
                continue
            _, basename = filename.split('-', 1)
            ignore.add(basename[:-4])

    def ignore_from_package(self, project, package, arch, ignore):
        """Extract rpm names from current build of package."""
        for binary in binary_list(self.apiurl, project, 'standard', arch, package):
            ignore.add(binary.name)

        return ignore

    def install_check(self, directory_project, directory_group, arch, ignore):
        self.logger.info('install check: start')

        with tempfile.NamedTemporaryFile() as ignore_file:
            # Print ignored rpms on separate lines in ignore file.
            for item in ignore:
                ignore_file.write(item + '\n')
            ignore_file.flush()

            # Invoke repo-checker.pl to perform an install check.
            script = os.path.join(SCRIPT_PATH, 'repo-checker.pl')
            parts = ['LC_ALL=C', 'perl', script, arch, directory_group,
                     '-r', directory_project, '-f', ignore_file.name]
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

            # Format output as markdown comment.
            code = '```\n'
            parts = []

            stdout = stdout.strip()
            if stdout:
                parts.append(code + stdout + '\n' + code)
            stderr = stderr.strip()
            if stderr:
                parts.append(code + stderr + '\n' + code)

            return CheckResult(False, ('\n' + ('-' * 80) + '\n\n').join(parts))


        self.logger.info('install check: passed')
        return CheckResult(True, None)

    def cycle_check(self, project, group, arch):
        if self.skip_cycle:
            self.logger.info('cycle check: skip due to --skip-cycle')
            return CheckResult(True, None)

        self.logger.info('cycle check: start')
        cycle_detector = CycleDetector(self.staging_api(project))
        comment = []
        for index, (cycle, new_edges, new_packages) in enumerate(
            cycle_detector.cycles(group, arch=arch), start=1):
            if new_packages:
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
            return CheckResult(False, '\n'.join(comment))

        self.logger.info('cycle check: passed')
        return CheckResult(True, None)

    def result_comment(self, project, group, arch, results, comment):
        """Generate comment from results"""
        comment.append('## ' + arch + '\n')
        if not results['cycle'].success:
            comment.append('### new [cycle(s)](/project/repository_state/{}/standard)\n'.format(group))
            comment.append(results['cycle'].comment + '\n')
        if not results['install'].success:
            comment.append('### [install check](/package/view_file/{}:Staging/dashboard/installcheck?expand=1)\n'.format(project))
            comment.append(results['install'].comment + '\n')

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
        if len(what_depends_on):
            self.logger.warn('{} still required by {}'.format(action.tgt_package, ', '.join(what_depends_on)))

        if len(self.comment_handler.lines):
            self.comment_write(result='decline')
            return False

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

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.skip_cycle:
            bot.skip_cycle = self.options.skip_cycle

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
