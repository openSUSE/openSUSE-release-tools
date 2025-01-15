import abc

class VCSBase(metaclass=abc.ABCMeta):
    """Base class for VCS implementations"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Get the name of VCS"""
        pass

    @abc.abstractmethod
    def get_path(self, *_args):
        """Issue a get to a specific path from the repository."""
        pass
