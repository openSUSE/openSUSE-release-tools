#!/usr/bin/python3
import os
import sys
import ReviewBot

import logging

import traceback

import re

import subprocess

import tempfile

from argparse import Namespace

from urllib.parse import urljoin, urldefrag
import urllib.error

from lxml import etree

from osc import conf
from osc.core import makeurl, make_meta_url, http_POST, http_PUT
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from pkglistgen.engine import Engine
from pkglistgen.tool import PkgListGen, MismatchedRepoException

from collections import namedtuple

from contextlib import contextmanager

try:
    from package_monkey.subcommands import PackageMonkey, subcommandRegistry

    is_package_monkey_available = True
except:
    print("Package Monkey not found")
    is_package_monkey_available = False

DEFAULT_AUTOGITS_REVIEWER = "autogits_obs_staging_bot"
DEFAULT_ENGINE = "package_monkey"  # "product_composer"
DEFAULT_ENABLE_REPOSITORIES = "product images"

STAGING_PROGRESS_MARKER = "staging/In Progress"
STAGING_TYPE_MARKERS = "QA-SLES-Basic QA-SLES-Reduced QA-SLES-Full"

slugify_regex = re.compile("[^a-z0-9_]+")

StagingProject = namedtuple(
    "StagingProject", ["target", "codebase_project", "name", "origin", "label"]
)


def slugify(x):
    return slugify_regex.sub("-", x.lower())


class MonkeyContext(PackageMonkey if is_package_monkey_available else object):
    """
    A Context Manager to execute Package Monkey commands.

    The following assumptions are made:
    - Only a single thread can access the context
    - The cache directory is shared with every context, and also
      between bot runs
    - The state directory is per-context and gets cleaned-up on
      context exit
    """

    DEFAULTS = {
        "codebase": "slfo",
        "verbose": True,
        "quiet": False,
    }

    def __init__(self, user_name, **context_opts):

        if not is_package_monkey_available:
            raise Exception(
                "Created a MonkeyContext without package-monkey being available"
            )

        super().__init__(user_name)

        # Use a different cache directory to differentiate with the standalone
        # tool.
        # This cache directory is *shared* between all the contextes
        # TODO: add user_name in the mix
        self.cache_directory = os.path.expanduser(
            "~/.cache/git-pkglistgen-package_monkey"
        )

        # Generate a per-context state directory.
        # Gets cleaned up on exit
        self.temporary_directory = tempfile.TemporaryDirectory(suffix="package_monkey")

        self.context_opts = {**self.DEFAULTS, **context_opts}
        self.context_opts["statedir"] = self.temporary_directory.name
        self.context_opts["cache"] = self.cache_directory

    def __run_command(self, cmd, *extra_args, **extra_kwargs):

        # self.initializeLogging(cmd)

        args = self.args.parse_args(args=[cmd.NAME, *extra_args])
        vars(args).update({**self.context_opts, **extra_kwargs})
        application = cmd.createApplication(args)

        try:
            return application.run()
        except SystemExit as e:
            if e.code > 0:
                raise Exception(f"package-monkey {cmd} call failed with exit code {e.code}")

    def __getattr__(self, attr):
        cmd = self.findSubcommand(attr, subcommandRegistry.commands)
        if cmd:
            return lambda *args, **kwargs: self.__run_command(cmd, *args, **kwargs)

        return super().__getattr__(attr)

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        # Clean the tempdir
        del self.temporary_directory


class GitObject(object):

    def __init__(self):
        # This gets cleaned up on exit
        self.temporary_directory = tempfile.TemporaryDirectory(suffix="pkglistgen")

        self.git_checkout = os.path.join(self.temporary_directory.name, "git")

    def __get_ref(self, pointer):
        if (
            subprocess.run(
                ["git", "rev-parse", "--verify", f"refs/heads/{pointer}"],
                cwd=self.git_checkout,
            ).returncode
            > 0
        ):
            # commit/tag
            return pointer
        else:
            # branch
            return f"refs/heads/{pointer}"

    def add(self, content: list):
        subprocess.check_call(
            ["git", "add"] + content,
            cwd=self.git_checkout,
        )

    def commit(self, message):
        subprocess.check_call(
            [
                "git",
                "commit",
                "-m",
                message,
            ],
            cwd=self.git_checkout,
        )

    def push_to_branch(self, source_pointer, target_remote, target_branch, force=False):
        subprocess.check_call(
            [
                "git",
                "push",
                "--force" if force else "--no-force",
                target_remote,
                f"{self.__get_ref(source_pointer)}:refs/heads/{target_branch}",
            ],
            cwd=self.git_checkout,
        )

    def push(self):
        subprocess.check_call(
            ["git", "push"],
            cwd=self.git_checkout,
        )

    def reset_to_ref(self, ref):
        subprocess.check_call(
            ["git", "reset", "--hard", ref],
            cwd=self.git_checkout,
        )


class GitWorktree(GitObject):

    @classmethod
    def from_repository(cls, repository, branch_name):
        obj = cls()
        subprocess.check_call(
            ["git", "worktree", "add", obj.git_checkout, branch_name],
            cwd=repository.git_checkout,
        )

        return obj


class GitRepository(GitObject):

    def __init__(self, origin_remote, mirror=False):

        super().__init__()

        self.origin_remote = origin_remote

        self.mirror = mirror

    def fetch(self):
        if not os.path.exists(self.git_checkout):
            subprocess.check_call(
                [
                    "git",
                    "clone",
                    "--mirror" if self.mirror else "--no-mirror",
                    self.origin_remote,
                    self.git_checkout,
                ]
            )

        # Fetch
        subprocess.check_call(
            ["git", "fetch", self.origin_remote], cwd=self.git_checkout
        )

    @contextmanager
    def transient_worktree(self, branch_name):
        worktree = GitWorktree.from_repository(self, branch_name)
        try:
            yield worktree
        finally:
            pass


class GitRepositories(object):

    def __init__(self):
        self.mapping = {}

    def register_repository(self, remote):
        return GitRepository(remote)

    def __getitem__(self, origin_remote):
        if origin_remote not in self.mapping:
            self.mapping[origin_remote] = self.register_repository(origin_remote)

        return self.mapping[origin_remote]


class GitMirrors(GitRepositories):

    def register_repository(self, remote):
        return GitRepository(remote, mirror=True)


class GitPkgListGenBot(ReviewBot.ReviewBot):
    """A review bot that runs pkglistgen on staging QA projects"""

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        conf.get_config()

        self.tool = PkgListGen()
        self.apiurl = conf.config["apiurl"]

        self.allowed_repositories = []

        self.staging_origin_cache = {}

        self.cloned_repositories = GitRepositories()
        self.mirrored_repositories = GitMirrors()

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
            yield StagingProject(
                target=f"{base_project}:{request_id}:{project['Name']}",
                codebase_project=f"{base_project}:{request_id}",
                origin=project["Origin"],
                name=project["Name"],
                label=project.get("Label"),
            )

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
            result, message = self.run_pkglistgen(
                src_owner, src_project, src_rev, target_owner, target_package
            )
        except Exception:
            self.review_messages["declined"] = (
                f"Unhandled exception:\n\n```{traceback.format_exc()}```"
            )
            return False

        if result:
            self.review_messages["accepted"] = message or "OK"

        return result  # True or None

    def run_pkglistgen(
        self, src_owner, src_project, src_rev, target_owner, target_package
    ):
        """
        Runs pkglistgen.

        :return: result: True (pkglistgen ran), or None (should skip/retry later).
                 message: a message that should be shown into the comment, or None.
        """

        request = self.get_request_from_src_rev(self.requests, src_rev)
        if not request:
            self.logger.warning(f"Request for src_rev {src_rev} not found")
            return None, None

        if f"{request._owner}/{request._repo}" not in self.allowed_repositories:
            self.logger.info(
                f"{request._owner}/{request._repo} is not in the allowed repositories list"
            )
            return None, None

        if STAGING_PROGRESS_MARKER not in request._labels:
            self.logger.info(
                f"PR {request._owner}/{request._repo}#{request._pr_id} is not in progress"
            )
            return None, None

        base_commit = request.actions[0].tgt_rev
        staging_configuration = self.get_git_staging_configuration(
            target_owner, target_package, base_commit
        )

        if "QA" not in staging_configuration:
            self.logger.warning(
                f"PR {request._owner}/{request._repo}#{request._pr_id} has no QA staging configured"
            )
            return None, None

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

        configured_engine = target_config.get("pkglistgen-engine", DEFAULT_ENGINE)
        if configured_engine != "package_monkey":
            pkglistgen_engine = Engine[configured_engine]

        enable_repositories = target_config.get(
            "pkglistgen-enable-repositories", DEFAULT_ENABLE_REPOSITORIES
        ).split(" ")
        staging_type_markers = target_config.get(
            "staging-type-markers", STAGING_TYPE_MARKERS
        ).split(" ")

        approver = target_config.get("pkglistgen-approver", DEFAULT_AUTOGITS_REVIEWER)
        if not self.is_request_approved_by(request, approver):
            return None, None

        if True not in [x in staging_type_markers for x in request._labels]:
            self.logger.info(
                f"PR {request._owner}/{request._repo}#{request._pr_id} has no valid staging markers set"
            )
            return True, "Not asked to create stagings. Accepting."

        staging_project_available = False
        for qa_project in self.get_qa_projects(request._pr_id, staging_configuration):
            api = StagingAPI(self.apiurl, qa_project.target)

            try:
                meta = api.get_prj_meta(qa_project.target)
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    # Let's go ahead, as the QA project might have been masked by the used Label,
                    # and we will check that further on
                    continue
                else:
                    raise
            else:
                staging_project_available = True

            # Obtain the target repository name by looking at the repository name
            # in the Origin's scmsync. We cannot use qa_project["Name"] in this case
            # as after labels have been introduced, the same origin might have multiple
            # QA projects
            if qa_project.origin not in self.staging_origin_cache:
                origin_meta = api.get_prj_meta(qa_project.origin)
                origin_git_url_element = origin_meta.xpath("/project/scmsync")[0]

                url, fragment = urldefrag(origin_git_url_element.text)
                self.staging_origin_cache[qa_project.origin] = url.split("/")[
                    -1
                ].replace(".git", "")

            staging_repo_url = urljoin(
                staging_org_url, self.staging_origin_cache[qa_project.origin]
            )
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
                self.mirrored_repositories[url].fetch()
                self.mirrored_repositories[url].push_to_branch(
                    fragment, staging_repo_url, staging_branch, force=True
                )

                git_url_element.text = target_git_url

                self.replace_meta(qa_project.target, meta)

                # We will get back to it later
                return None, None

            try:
                if configured_engine == "package_monkey":
                    # Download model
                    model_url = "https://src.suse.de/sle-prjmgr/SLFO.git"
                    self.cloned_repositories[model_url].fetch()
                    self.cloned_repositories[model_url].reset_to_ref(
                        "remotes/origin/main"
                    )

                    with MonkeyContext(
                        "user_name",
                        codebase="slfo",
                        extra_build_project=[
                            qa_project.codebase_project,
                            qa_project.target,
                        ],
                        model_path=self.cloned_repositories[model_url].git_checkout,
                    ) as monkey:
                        monkey.download()
                        monkey.prepare(ignore_errors=True)
                        monkey.classify()

                        staging_repo = self.mirrored_repositories[staging_repo_url]
                        staging_repo.fetch()
                        with staging_repo.transient_worktree(
                            staging_branch
                        ) as worktree:
                            monkey.compose(build_path=worktree.git_checkout)
                            monkey.publish(
                                os.path.join(
                                    worktree.git_checkout, "000productcompose"
                                ),
                                scope="compose",
                            )

                            # package-monkey only outputs productcompose files
                            worktree.add(["000productcompose/default.productcompose"])
                            worktree.commit("Package list update by package-monkey")
                            worktree.push()
                else:
                    self.tool.reset()
                    self.tool.dry_run = self.dryrun
                    self.tool.update_and_solve_target(
                        api,
                        main_project,
                        target_config,
                        main_repo,
                        git_url=git_url_element.text,
                        project=qa_project.target,
                        scope="target",
                        engine=pkglistgen_engine,
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
                return None, None
            else:
                # Enable builds
                if not self.dryrun:
                    for repository in enable_repositories:
                        self.set_project_flag(
                            qa_project.target, "build", repository, "enable"
                        )

        if staging_project_available:
            return True, "pkglistgen ran successfully"
        else:
            self.logger.info(
                "Staging bot didn't create the QA project, but accepted the review. Nothing to do."
            )
            return (
                True,
                "Staging bot didn't create the QA project, but accepted the review. Nothing to do.",
            )


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
