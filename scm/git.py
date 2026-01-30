import scm.base

import git
import os
import shutil


class Git(scm.base.SCMBase):
    """SCM interface implementation for Git"""

    def __init__(self, logger, base_url):
        self.logger = logger
        self.base_url = base_url
        pass

    @property
    def name(self) -> str:
        return "GIT"

    @staticmethod
    def submodule_diff(
        repo: git.Repo,
        base_commit: str,
        head_remote_name: str,
        head_remote_url: str,
        head_commit: str,
    ):
        repo.remotes.origin.fetch([base_commit])

        head_remote = repo.create_remote(head_remote_name, head_remote_url)
        head_remote.fetch([head_commit])

        # TODO: It'd be awesome to use GitPython's structured DiffIndex objects, but the library still does not support sha256 object
        # format. See https://github.com/gitpython-developers/GitPython/issues/1475
        return repo.git.diff("--submodule", f"{base_commit}..{head_commit}")

    def package_url(self, target_project: str, target_package: str) -> str:
        return f"{self.base_url}/{target_project}/{target_package}.git"

    def clone_repository(self, url: str, dstpath: str, **kwargs):
        repo = git.Repo.clone_from(url, dstpath)

        revision = kwargs.get("revision")
        if revision is not None:
            repo.remotes.origin.fetch([revision])
            repo.git.checkout(revision)

        return repo

    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        dstpath = os.path.join(pathname, target_package)
        url = self.package_url(target_project, target_package)

        self.clone_repository(url, dstpath, **kwargs)

        for i in ['.github', '.gitea', '.git']:
            shutil.rmtree(os.path.join(dstpath, i), ignore_errors=True)
