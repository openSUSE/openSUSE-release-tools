import vcs.base

import shutil
import json
import os

class Action(vcs.base.VCSBase):
    """Stub VCS interface implementation for running as an action"""

    @staticmethod
    def _read_context():
        env = os.environ["GITHUB_CONTEXT"]
        return json.loads(env)

    def __init__(self, logger):
        self.logger = logger
        self.context = Action._read_context()
        self.logger.debug(f'Github Context: {self.context}')

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
        # XXX verify `target_project` & `target_package`?
        src = os.environ["GITHUB_WORKSPACE"]
        dst = f'{pathname}/{target_package}'
        self.logger.debug(f'checkout: {src} -> {dst}')
        shutil.copytree(src, dst)
