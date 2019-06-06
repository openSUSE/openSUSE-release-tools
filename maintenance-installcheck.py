#!/usr/bin/python3

import sys

import osc.core

import ReviewBot
from osclib.conf import Config, str2bool
from osclib.core import (repository_path_expand, repository_path_search,
                         target_archs)
from osclib.repochecks import installcheck, mirror


class MaintInstCheck(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.request_default_return = True

    def repository_check(self, repository_pairs, archs):
        project, repository = repository_pairs[0]
        self.logger.info('checking {}/{}'.format(project, repository))

        if not len(archs):
            self.logger.debug(
                '{} has no relevant architectures'.format(project))
            return

        for arch in archs:
            directories = []
            for pair_project, pair_repository in repository_pairs:
                directories.append(
                    mirror(self.apiurl, pair_project, pair_repository, arch))

            parts = installcheck(directories, arch, [], [])
            if len(parts):
                self.comment.append(
                    '## {}/{}\n'.format(repository_pairs[0][1], arch))
                self.comment.extend(parts)

        return len(self.comment) == 0

    def check_one_request(self, req):
        self.comment = []
        self.checked_targets = set()
        overall = super(MaintInstCheck, self).check_one_request(req)
        if len(self.comment):
            msg = '\n'.join(self.comment)
            self.logger.debug(msg)
            if not self.dryrun:
                osc.core.change_review_state(apiurl=self.apiurl,
                                             reqid=req.reqid, newstate='declined',
                                             by_group=self.review_group,
                                             by_user=self.review_user, message=msg)
            # lie to the super class - decline only once
            return None

        return overall

    def check_action_maintenance_release(self, request, action):
        # No reason to special case patchinfo since same source and target
        # projects which is all that repo_checker cares about.

        if action.tgt_project in self.checked_targets:
            return True

        target_config = Config.get(self.apiurl, action.tgt_project)
        if str2bool(target_config.get('repo_checker-project-skip', 'False')):
            # Do not change message as this should only occur in requests
            # targeting multiple projects such as in maintenance workflow in
            # which the message should be set by other actions.
            self.logger.debug(
                'skipping review of action targeting {}'.format(action.tgt_project))
            return True

        repository = target_config.get('main-repo')
        if not repository:
            raise Exception('Missing main-repo in OSRT:Config')

        # Find a repository which links to target project "main" repository.
        repository = repository_path_search(
            self.apiurl, action.src_project, action.tgt_project, repository)
        if not repository:
            raise Exception('Missing repositories')

        repository_pairs = repository_path_expand(
            self.apiurl, action.src_project, repository)

        self.checked_targets.add(action.tgt_project)
        archs = set(target_archs(self.apiurl, action.src_project, repository))
        arch_whitelist = target_config.get('repo_checker-arch-whitelist', None)
        if arch_whitelist:
            archs = set(arch_whitelist.split(' ')).intersection(archs)

        if not self.repository_check(repository_pairs, archs):
            return None

        self.review_messages['accepted'] = 'install check passed'
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = MaintInstCheck

if __name__ == '__main__':
    app = CommandLineInterface()
    sys.exit(app.main())
