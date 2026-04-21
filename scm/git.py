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
        head_revision_name=None,
    ):

        repo = Git.checkout_revision(repo, base_commit)

        repo = Git.checkout_revision(
            repo, head_commit, revision_name=head_revision_name, remote=head_remote_name, remote_url=head_remote_url
        )
        head_revision = head_revision_name if head_revision_name else head_commit

        try:
            # If the change can be fast-forwarded on top of base, we can try checking exactly the
            # changes that are brought in.
            repo.git.rebase(base_commit)
            rebased = True
            self.logger.info(f"Successfully rebased {head_revision} upon {base_commit}.")
        except git.exc.GitCommandError:
            repo.git.rebase("--abort")
            rebased = False

        # TODO: It'd be awesome to use GitPython's structured DiffIndex objects, but the library still does not support sha256 object
        # format. See https://github.com/gitpython-developers/GitPython/issues/1475
        diff = repo.git.diff("--submodule", f"{base_commit}..{head_revision}")

        # Cleanup remote
        if head_remote_name != "origin":
            repo.delete_remote(head_remote_name)

        return diff, rebased

    @staticmethod
    def checkout_revision(repo, revision: str, revision_name=None, remote="origin", remote_url=None, fetch=True):
        if not isinstance(repo, git.Repo):
            repo = git.Repo(repo)

        if fetch:
            if remote_url and remote not in set(r.name for r in repo.remotes):
                repo.create_remote(remote, remote_url)

            current_revision = repo.git.rev_parse("--abbrev-ref", "HEAD")
            if current_revision == revision and revision_name is None:
                repo.remote(remote).pull(revision)
            else:
                if revision_name is not None:
                    to_fetch = f"{revision}:{revision_name}"
                else:
                    to_fetch = f"{revision}:{revision}"
                repo.remote(remote).fetch([to_fetch])

        if revision_name is not None:
            repo.git.switch(revision_name)
        else:
            repo.git.checkout(revision, "--")

        return repo

    def package_url(self, target_project: str, target_package: str) -> str:
        return f"{self.base_url}/{target_project}/{target_package}.git"

    def clone_repository(self, url: str, dstpath: str, **kwargs):
        repo = git.Repo.clone_from(url, dstpath)

        revision = kwargs.get("revision")
        revision_name = kwargs.get("revision_name")
        if revision is not None:
            Git.checkout_revision(repo, revision, revision_name=revision_name)

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
