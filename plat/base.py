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

    @abc.abstractmethod
    def get_project_config(self, project):
        """Get project config"""
        pass

    @abc.abstractmethod
    def get_request_age(self, request):
        """Get the age of a request"""
        pass

    @abc.abstractmethod
    def get_request_list_with_history(
            self, project='', package='', req_who='', req_state=('new', 'review', 'declined'),
            req_type=None, exclude_target_projects=[]):
        """Get requests with full history"""
        pass

    @abc.abstractmethod
    def get_staging_api(self, project):
        """Get staging API for the project"""
        pass

    @abc.abstractmethod
    def search_review(self, **kwargs):
        """Search review requests according to specified criteria"""
        pass

    @abc.abstractmethod
    def can_accept_review(self, req, **kwargs):
        """Check whether it is possible to accept review for a request"""
        pass

    @abc.abstractmethod
    def change_review_state(self, req, newstate, message, **kwargs):
        """Change review state for a request"""
        pass
