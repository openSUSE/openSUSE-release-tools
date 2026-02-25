#!/usr/bin/python3
import os
import sys
import ReviewBot

import logging

import traceback

import urllib.error

import shutil

from osc import conf
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from staginginstallchecker.installchecker import InstallChecker, CheckResult

from osclib.cache_manager import CacheManager

DEFAULT_AUTOGITS_REVIEWER = "autogits_obs_staging_bot"
DEFAULT_ARCHITECTURES = "x86_64 s390x ppc64le aarch64"

CACHEDIR = CacheManager.directory("repository-meta")


class GitInstallCheckBot(ReviewBot.ReviewBot):
    """A review bot that runs staging-installcheck on staging QA projects"""

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        conf.get_config()

        self.apiurl = conf.config["apiurl"]

        self.allowed_repositories = []

        # This is heavily dependent on the GITEA platform
        if self.platform.name != "GITEA":
            raise Exception("Unsupported platform: this bot is only supported on Gitea")

    def get_git_staging_configuration(self, owner, project, commit_sha):
        # FIXME: support JWCC
        return self.platform.get_path(
            f"repos/{owner}/{project}/raw/staging.config?ref={commit_sha}"
        ).json()

    @staticmethod
    def is_request_approved_by(request, approver):
        for review in request.reviews:
            if review.by == approver and review.state == "accepted":
                # We skip dismissed reviews, so we can afford returning
                # as soon as we find a matching review
                return True

        return False

    @staticmethod
    def get_request_from_src_rev(requests, src_rev):
        for request in requests:
            if request.actions[0].src_rev == src_rev:
                return request

        return None

    def check_source_submission(
        self, src_owner, src_project, src_rev, target_owner, target_package
    ):
        self.logger.info(f"Checking {src_project}: {src_owner} -> {target_owner}")

        try:
            result = self.run_installcheck(
                src_owner, src_project, src_rev, target_owner, target_package
            )
        except Exception:
            self.review_messages["declined"] = (
                f"Unhandled exception:\n\n```{traceback.format_exc()}```"
            )
            return False

        if result is None:
            return None
        elif result.success:
            self.review_messages["accepted"] = "installcheck ran successfully"
        else:
            self.review_messages["declined"] = "\n".join(result.comment)

        return result.success

    def run_installcheck(
        self, src_owner, src_project, src_rev, target_owner, target_package
    ):
        """
        Runs repo_checker.

        :return: either a CheckResult, or None (should skip/retry later)
        """

        request = self.get_request_from_src_rev(self.requests, src_rev)
        if not request:
            self.logger.warning(f"Request for src_rev {src_rev} not found")
            return None

        if f"{request._owner}/{request._repo}" not in self.allowed_repositories:
            self.logger.info(
                f"{request._owner}/{request._repo} is not in the allowed repositories list"
            )
            return None

        base_commit = request.actions[0].tgt_rev
        staging_configuration = self.get_git_staging_configuration(
            target_owner, target_package, base_commit
        )

        main_project = staging_configuration["ObsProject"]

        codestream_project = (
            f"{staging_configuration['StagingProject']}:{request._pr_id}"
        )

        Config(self.apiurl, main_project)
        target_config = conf.config[main_project]

        main_repo = target_config["main-repo"]

        enabled_architectures = target_config.get(
            "repo_checker-arch-whitelist", DEFAULT_ARCHITECTURES
        ).split(" ")

        approver = target_config.get("repo_checker-approver", DEFAULT_AUTOGITS_REVIEWER)
        if not self.is_request_approved_by(request, approver):
            return None

        api = StagingAPI(self.apiurl, codestream_project)
        tool = InstallChecker(api, target_config)

        try:
            api.get_prj_meta(codestream_project)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return CheckResult(
                    success=True, comment="Staging bot didn't create a project"
                )
            else:
                raise

        try:
            return tool.staging_installcheck(
                codestream_project, main_repo, enabled_architectures, devel=True
            )
        finally:
            # Clean-up dynamic PR data - in the git workflow we
            # have dynamic build projects, so the repo_mirrorer's stale
            # object clean-up won't trigger
            project_cache = os.path.join(CACHEDIR, codestream_project)

            if os.path.exists(project_cache) and not os.path.exists(
                os.path.join(project_cache, ".lock")
            ):
                # Lock being present should actually never happen - we
                # are the only users, and we run the check sequentially,
                # however, let's check for a lock file anyway. Better safe
                # than sorry.
                self.logger.debug(f"Cleaning up {project_cache}")
                shutil.rmtree(project_cache)
            else:
                # If the lock file is present, log an error, but don't
                # try to remove it
                self.logger.error(
                    f"{project_cache} has a lock file, and cannot be removed. This shouldn't happen. Skipping cleanup"
                )


class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, *kwargs)
        self.clazz = GitInstallCheckBot

    def get_optparser(self):
        parser = super().get_optparser()

        # Add bot-specific options
        # If ReviewBot/Cmdln moves to ArgumentParser, we can turn this into a
        # string directly and use nargs=*.
        parser.add_option(
            "--git-allow-repos",
            default="",
            help="allowed git repositories (e.g. products/SLFO,products/SLES)",
        )

        return parser

    def setup_checker(self):
        instance = super().setup_checker()

        instance.allowed_repositories = self.options.git_allow_repos.split(",")

        return instance


if __name__ == "__main__":
    app = CommandLineInterface()
    logging.basicConfig(level=logging.DEBUG)

    sys.exit(app.main())
