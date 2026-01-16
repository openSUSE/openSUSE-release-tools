#!/usr/bin/python3
import sys
import ReviewBot

import logging

import traceback

from osc import conf
from osc.core import makeurl, http_POST
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from pkglistgen.engine import Engine
from pkglistgen.tool import PkgListGen, MismatchedRepoException

DEFAULT_AUTOGITS_REVIEWER = "autogits_obs_staging_bot"
DEFAULT_ENGINE = "product_composer"
DEFAULT_ENABLE_REPOSITORIES = "product images"


class GitPkgListGenBot(ReviewBot.ReviewBot):
    """A review bot that runs pkglistgen on staging QA projects"""

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        conf.get_config()

        self.tool = PkgListGen()
        self.apiurl = conf.config["apiurl"]

        # This is heavily dependent on the GITEA platform
        if self.platform.name != "GITEA":
            raise Exception("Unsupported platform: this bot is only supported on Gitea")

    def get_git_staging_configuration(self, owner, project, commit_sha):
        # FIXME: support JWCC
        return self.platform.get_path(
            f"repos/{owner}/{project}/raw/staging.config?ref={commit_sha}"
        ).json()

    def get_qa_projects(self, request_id, staging_configuration):
        base_project = staging_configuration["StagingProject"]
        for project in staging_configuration.get("QA", []):
            yield f"{base_project}:{request_id}:{project['Name']}"

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

    def set_project_flag(self, project, flag, repository, status):
        return http_POST(
            makeurl(
                self.apiurl,
                ["source", project],
                {
                    "cmd": "set_flag",
                    "flag": flag,
                    "repository": repository,
                    "status": status,
                },
            )
        )

    def check_source_submission(
        self, src_owner, src_project, src_rev, target_owner, target_package
    ):
        self.logger.info(f"Checking {src_project}: {src_owner} -> {target_owner}")

        try:
            result = self.run_pkglistgen(
                src_owner, src_project, src_rev, target_owner, target_package
            )
        except Exception:
            self.review_messages["declined"] = (
                f"Unhandled exception:\n\n```{traceback.format_exc()}```"
            )
            return False

        if result:
            self.review_messages["accepted"] = "pkglistgen ran successfully"

        return result  # True or None

    def run_pkglistgen(
        self, src_owner, src_project, src_rev, target_owner, target_package
    ):
        """
        Runs pkglistgen.

        :return: either True (pkglistgen ran), or None (should skip/retry later)
        """

        request = self.get_request_from_src_rev(self.requests, src_rev)
        if not request:
            self.logger.warning(f"Request for src_rev {src_rev} not found")
            return None

        base_commit = request.actions[0].tgt_rev
        staging_configuration = self.get_git_staging_configuration(
            target_owner, target_package, base_commit
        )

        if "QA" not in staging_configuration:
            self.logger.warning(
                f"PR {request._owner}/{request._repo}#{request._pr_id} has no QA staging configured"
            )
            return None

        main_project = staging_configuration["ObsProject"]

        Config(self.apiurl, main_project)
        target_config = conf.config[main_project]

        main_repo = target_config["main-repo"]
        engine = Engine[target_config.get("pkglistgen-engine", DEFAULT_ENGINE)]
        enable_repositories = target_config.get(
            "pkglistgen-enable-repositories", DEFAULT_ENABLE_REPOSITORIES
        ).split(" ")

        approver = target_config.get("pkglistgen-approver", DEFAULT_AUTOGITS_REVIEWER)
        if not self.is_request_approved_by(request, approver):
            return None

        for project_name in self.get_qa_projects(request._pr_id, staging_configuration):
            api = StagingAPI(self.apiurl, project_name)

            meta = api.get_prj_meta(project_name)
            git_url = meta.xpath("/project/scmsync")[0].text

            self.tool.reset()
            self.tool.dry_run = self.dryrun
            try:
                self.tool.update_and_solve_target(
                    api,
                    main_project,
                    target_config,
                    main_repo,
                    git_url=git_url,
                    project=project_name,
                    scope="target",
                    engine=engine,
                    force=True,
                    no_checkout=False,
                    only_release_packages=False,
                    only_update_weakremovers=False,
                    stop_after_solve=False,
                    custom_cache_tag="git-pkglistgen",
                )
            except MismatchedRepoException:
                # Repo still building, just exit now as presumably eventual
                # other projects are also affected
                self.logger.warning("Repository is still building, trying next time...")
                return None
            else:
                # Enable builds
                if not self.dryrun:
                    for repository in enable_repositories:
                        self.set_project_flag(
                            project_name, "build", repository, "enable"
                        )

        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = GitPkgListGenBot


if __name__ == "__main__":
    app = CommandLineInterface()
    logging.basicConfig(level=logging.DEBUG)

    sys.exit(app.main())
