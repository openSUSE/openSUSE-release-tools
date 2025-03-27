import abc

class VCSBase(metaclass=abc.ABCMeta):
    """Base class for VCS implementations"""

    @abc.abstractmethod
    def get_path(self, *_args):
        """Issue a get to a specific path from the repository."""
        pass

    @abc.abstractmethod
    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        """Checkout a package"""
        pass
