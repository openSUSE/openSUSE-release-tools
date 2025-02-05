import abc

class PlatformBase(metaclass=abc.ABCMeta):
    """Base class for platform implementations"""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Get the name of the platform"""
        pass

    @abc.abstractmethod
    def get_request(self, request_id, with_full_history=False):
        """Get request by id"""
        pass
