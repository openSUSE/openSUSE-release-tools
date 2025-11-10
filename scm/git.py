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

    def get_path(self, *args):
        # XXX stub
        raise NotImplementedError

    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        dstpath = os.path.join(pathname, target_package)
        url = f"{self.base_url}/{target_project}/{target_package}.git"
        repo = git.Repo.clone_from(url, dstpath)

        revision = kwargs.get('revision')
        if revision is not None:
            repo.remotes.origin.fetch([revision])
            repo.git.checkout(revision)

        for i in ['.github', '.gitea', '.git']:
            shutil.rmtree(os.path.join(dstpath, i), ignore_errors=True)
