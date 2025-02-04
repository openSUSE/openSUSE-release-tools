import vcs.base

class Git(vcs.base.VCSBase):
    """VCS interface implementation for Git"""

    def __init__(self):
        # XXX stub
        pass

    @property
    def name(self) -> str:
        return "git"

    def get_path(self, *args):
        # XXX stub
        raise NotImplementedError

    def get_request(self, request_id, with_full_history=False):
        # XXX stub
        raise NotImplementedError

    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        # XXX stub
        raise NotImplementedError
