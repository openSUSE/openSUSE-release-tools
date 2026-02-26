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

    def submodule_diff(
        self,
        repo: git.Repo,
        base_commit: str,
        head_remote_name: str,
        head_remote_url: str,
        head_commit: str,
    ):

        repo = Git.checkout_revision(repo, base_commit)

        repo = Git.checkout_revision(repo, head_commit, remote=head_remote_name, remote_url=head_remote_url)

        try:
            # If the change can be fast-forwarded on top of base, we can try checking exactly the
            # changes that are brought in.
            repo.git.rebase(base_commit)
            rebased = True
            self.logger.debug(f"Successfully rebased {head_commit} upon {base_commit}.")
        except git.exc.GitCommandError:
            repo.git.rebase("--abort")
            rebased = False

        # TODO: It'd be awesome to use GitPython's structured DiffIndex objects, but the library still does not support sha256 object
        # format. See https://github.com/gitpython-developers/GitPython/issues/1475
        diff = repo.git.diff("--submodule", f"{base_commit}..{head_commit}")

        # Cleanup remote
        if head_remote_name != "origin":
            repo.delete_remote(head_remote_name)

        return diff, rebased

    @staticmethod
    def checkout_revision(repo, revision: str, remote="origin", remote_url=None, fetch=True):
        if not isinstance(repo, git.Repo):
            repo = git.Repo(repo)
        if fetch:
            if remote_url and remote not in set(r.name for r in repo.remotes):
                repo.create_remote(remote, remote_url)

            repo.remote(remote).fetch([revision])
        repo.git.checkout(revision)
        return repo

    def package_url(self, target_project: str, target_package: str) -> str:
        return f"{self.base_url}/{target_project}/{target_package}.git"

    def clone_repository(self, url: str, dstpath: str, **kwargs):
        repo = git.Repo.clone_from(url, dstpath)

        revision = kwargs.get("revision")
        if revision is not None:
            Git.checkout_revision(repo, revision)

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
