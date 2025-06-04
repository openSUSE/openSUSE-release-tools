import scm.base

import os
import shutil

import pygit2

class Git(scm.base.SCMBase):
    """SCM interface implementation for Git"""

    def __init__(self, logger, base_url):
        # XXX stub
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
        repo = pygit2.clone_repository(url, dstpath)

        revision = kwargs.get('revision')
        if revision is not None:
            oid = pygit2.Oid(hex=revision)
            commit = repo.get(oid)
            repo.checkout_tree(commit.tree, strategy=pygit2.GIT_CHECKOUT_FORCE)

        for i in ['.github', '.gitea', '.git']:
            shutil.rmtree(os.path.join(dstpath, i), ignore_errors=True)
