import scm.base

import shutil
import os


class Action(scm.base.SCMBase):
    """Stub SCM interface implementation for running as an action"""

    def __init__(self, logger):
        self.logger = logger

    @property
    def name(self) -> str:
        return "ACTION"

    def get_path(self, *args):
        raise NotImplementedError

    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        head_full_name = os.environ["PR_SRC_FULL_NAME"]
        head_project, head_package = head_full_name.split('/', 1)
        base_full_name = os.environ["PR_DST_FULL_NAME"]
        base_project, base_package = base_full_name.split('/', 1)
        workspace = os.environ["GITHUB_WORKSPACE"]

        if target_project == f"head:{head_project}" and target_package == head_package:
            src = f"{workspace}/head"
        elif target_project == f"base:{base_project}" and target_package == base_package:
            src = f"{workspace}/base"
        else:
            raise RuntimeError(f"Invalid checkout target: ${target_project}/${target_package}")

        dst = f'{pathname}/{target_package}'
        self.logger.debug(f'checkout: {target_project}/{target_package} {src} -> {dst}')
        shutil.copytree(src, dst)
        for i in ['.github', '.gitea', '.git']:
            shutil.rmtree(os.path.join(dst, i), ignore_errors=True)
