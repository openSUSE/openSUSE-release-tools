import scm.base


class Git(scm.base.SCMBase):
    """SCM interface implementation for Git"""

    def __init__(self):
        # XXX stub
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
        # XXX stub
        raise NotImplementedError
