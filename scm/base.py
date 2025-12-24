import abc


class SCMBase(metaclass=abc.ABCMeta):
    """Base class for VCS implementations"""

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
