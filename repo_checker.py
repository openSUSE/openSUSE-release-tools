#!/usr/bin/python

import sys

from osc import conf
from osclib.conf import Config
from osclib.core import depends_on
from osclib.core import maintainers_get
from osclib.core import request_staged
from osclib.core import target_archs
from osclib.stagingapi import StagingAPI

import ReviewBot

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
            if str(status['overall_state']) not in ('testing', 'review', 'acceptable'):
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

    def target_archs(self, project):
        archs = target_archs(self.apiurl, project)

        # Check for arch whitelist and use intersection.
        product = project.split(':Staging:', 1)[0]
        whitelist = self.staging_config[product].get('repo_checker-arch-whitelist')
        if whitelist:
            archs = list(set(whitelist.split(' ')).intersection(set(archs)))

        # Trick to prioritize x86_64.
        return reversed(archs)

    def check_action_delete(self, request, action):
        creator = request.get_creator()
        # Force include project maintainers in addition to package owners.
        maintainers = set(maintainers_get(self.apiurl, action.tgt_project, action.tgt_package) +
                          maintainers_get(self.apiurl, action.tgt_project)) # TODO Devel project
        if creator not in maintainers:
            self.logger.warn('{} is not one of the maintainers: {}'.format(creator, ', '.join(maintainers)))

        # TODO Include runtime dependencies instead of just build dependencies.
        what_depends_on = depends_on(self.apiurl, action.tgt_project, 'standard', [action.tgt_package], True)
        if len(what_depends_on):
            self.logger.warn('{} still required by {}'.format(action.tgt_package, ', '.join(what_depends_on)))

        if len(self.comment_handler.lines):
            self.comment_write(result='decline')
            return False

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
