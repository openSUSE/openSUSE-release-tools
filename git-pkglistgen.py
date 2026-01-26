#!/usr/bin/python3
import os
import sys
import ReviewBot

import logging

import traceback

import re

import subprocess

import tempfile

from urllib.parse import urljoin, urldefrag

from lxml import etree

from osc import conf
from osc.core import makeurl, make_meta_url, http_POST, http_PUT
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from pkglistgen.engine import Engine
from pkglistgen.tool import PkgListGen, MismatchedRepoException

DEFAULT_AUTOGITS_REVIEWER = "autogits_obs_staging_bot"
DEFAULT_ENGINE = "product_composer"
DEFAULT_ENABLE_REPOSITORIES = "product images"

STAGING_PROGRESS_MARKER = "staging/In Progress"

slugify_regex = re.compile("[^a-z0-9_]+")


def slugify(x):
    return slugify_regex.sub("-", x.lower())


class GitRepository(object):

    def __init__(self, origin_remote):

        self.origin_remote = origin_remote

        # This gets cleaned up on exit
        self.temporary_directory = tempfile.TemporaryDirectory(suffix="pkglistgen")

        self.git_checkout = os.path.join(self.temporary_directory.name, "git")

    def fetch(self):
        if not os.path.exists(self.git_checkout):
            subprocess.check_call(
                ["git", "clone", "--mirror", self.origin_remote, self.git_checkout]
            )

        # Fetch
        subprocess.check_call(
            ["git", "fetch", self.origin_remote], cwd=self.git_checkout
        )

    def push_to_branch(self, source_pointer, target_remote, target_branch):

        if (
            subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{source_pointer}"],
                cwd=self.git_checkout,
            ).returncode
            > 0
        ):
            # commit/tag
            source_ref = source_pointer
        else:
            # branch
            source_ref = f"refs/heads/{source_pointer}"

        subprocess.check_call(
            [
                "git",
                "push",
                target_remote,
                f"{source_ref}:refs/heads/{target_branch}",
            ],
            cwd=self.git_checkout,
        )


class GitRepositories(object):

    def __init__(self):
        self.mapping = {}

    def __getitem__(self, origin_remote):
        if origin_remote not in self.mapping:
            self.mapping[origin_remote] = GitRepository(origin_remote)

        return self.mapping[origin_remote]


class GitPkgListGenBot(ReviewBot.ReviewBot):
    """A review bot that runs pkglistgen on staging QA projects"""

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        conf.get_config()

        self.tool = PkgListGen()
        self.apiurl = conf.config["apiurl"]

        self.allowed_repositories = []
        self.cloned_repositories = GitRepositories()

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

    def replace_meta(self, project, meta_element: etree.ElementTree):
        return http_PUT(
            make_meta_url("prj", project, self.apiurl),
            data=etree.tostring(meta_element, encoding="utf-8", xml_declaration=True),
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

        if f"{request._owner}/{request._repo}" not in self.allowed_repositories:
            self.logger.info(
                f"{request._owner}/{request._repo} is not in the allowed repositories list"
            )

        if STAGING_PROGRESS_MARKER not in request._labels:
            self.logger.info(
                f"PR {request._owner}/{request._repo}#{request._pr_id} is not in progress"
            )
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
        staging_org_url = target_config["pkglistgen-git-staging-org-url"]
        if not staging_org_url.endswith("/"):
            staging_org_url += "/"
        staging_branch = slugify(
            f"qa_{request._owner}_{request._repo}_pr{request._pr_id}"
        )
        engine = Engine[target_config.get("pkglistgen-engine", DEFAULT_ENGINE)]
        enable_repositories = target_config.get(
            "pkglistgen-enable-repositories", DEFAULT_ENABLE_REPOSITORIES
        ).split(" ")

        approver = target_config.get("pkglistgen-approver", DEFAULT_AUTOGITS_REVIEWER)
        if not self.is_request_approved_by(request, approver):
            return None

        for project_name in self.get_qa_projects(request._pr_id, staging_configuration):
            target_repository_name = project_name.split(":")[-1]
            api = StagingAPI(self.apiurl, project_name)

            meta = api.get_prj_meta(project_name)
            staging_repo_url = urljoin(staging_org_url, target_repository_name)
            target_git_url = urljoin(staging_repo_url, f"#{staging_branch}")
            git_url_element = meta.xpath("/project/scmsync")[0]

            if not git_url_element.text.startswith(
                "http"
            ) or not target_git_url.startswith("http"):
                # We do not expect nor support non-http[s] uris
                raise Exception("Only http[s] git remote uris are supported")

            if git_url_element.text != target_git_url and not self.dryrun:
                # Should do the initial push
                url, fragment = urldefrag(git_url_element.text)
                self.logger.info(f"Creating branch {staging_branch}")
                self.cloned_repositories[url].fetch()
                self.cloned_repositories[url].push_to_branch(
                    fragment, staging_repo_url, staging_branch
                )

                git_url_element.text = target_git_url

                self.replace_meta(project_name, meta)

                # We will get back to it later
                return None

            self.tool.reset()
            self.tool.dry_run = self.dryrun
            try:
                self.tool.update_and_solve_target(
                    api,
                    main_project,
                    target_config,
                    main_repo,
                    git_url=git_url_element.text,
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
        super().__init__(*args, *kwargs)
        self.clazz = GitPkgListGenBot

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
