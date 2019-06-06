#!/usr/bin/python3

import logging
import os
import sys
from collections import namedtuple

from osc import conf

import ToolBase
from osclib.cache_manager import CacheManager
from osclib.conf import Config
from osclib.core import (repository_path_expand, repository_path_search,
                         target_archs, project_pseudometa_file_ensure)
from osclib.repochecks import mirror, installcheck

class RepoChecker():
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def project_only(self, project):
        repository = self.project_repository(project)
        if not repository:
            self.logger.error('a repository must be specified via OSRT:Config main-repo for {}'.format(project))
            return

        config = Config.get(self.apiurl, project)
        arch_whitelist = config.get('repo_checker-arch-whitelist')

        repository_pairs = repository_path_expand(self.apiurl, project, repository)
        self.repository_check(repository_pairs, arch_whitelist=arch_whitelist)

    def target_archs(self, project, repository, arch_whitelist=None):
        archs = target_archs(self.apiurl, project, repository)

        # Check for arch whitelist and use intersection.
        if arch_whitelist:
            archs = list(set(arch_whitelist.split(' ')).intersection(set(archs)))

        # Trick to prioritize x86_64.
        return sorted(archs, reverse=True)

    def project_pseudometa_file_name(self, project, repository):
        filename = 'repo_checker'

        main_repo = Config.get(self.apiurl, project).get('main-repo')
        if not main_repo:
            filename += '.' + repository

        return filename

    def repository_check(self, repository_pairs, arch_whitelist=None):
        project, repository = repository_pairs[0]
        self.logger.info('checking {}/{}@[{}]'.format(
            project, repository, len(repository_pairs)))

        archs = self.target_archs(project, repository, arch_whitelist)
        if not len(archs):
            self.logger.debug('{} has no relevant architectures'.format(project))
            return None

        result = True
        comment = []
        for arch in archs:
            directories = []
            for pair_project, pair_repository in repository_pairs:
                directories.append(mirror(self.apiurl, pair_project, pair_repository, arch))

            parts = installcheck(directories, arch, [], [])
            if len(parts):
                comment.append('## {}/{}\n'.format(repository_pairs[0][1], arch))
                comment.extend(parts)

        text = '\n'.join(comment).strip()
        if not self.dryrun:
            filename = self.project_pseudometa_file_name(project, repository)
            project_pseudometa_file_ensure(self.apiurl, project, filename, text + '\n', 'repo_checker project_only run')
        else:
            print(text)

        return result

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


class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def setup_tool(self):
        tool = RepoChecker()
        if self.options.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.options.verbose:
            logging.basicConfig(level=logging.INFO)

        return tool

    def do_project_only(self, subcmd, opts, project):
        self.tool.apiurl = conf.config['apiurl']
        self.tool.project_only(project)


if __name__ == '__main__':
    app = CommandLineInterface()
    sys.exit(app.main())
