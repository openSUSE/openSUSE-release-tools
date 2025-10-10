import plat.base

from dateutil.parser import parse as date_parse
import os


class CommentAPI:
    """Stub CommentAPI implementation"""
    def __init__(self):
        pass

    def get_comments(self, **kwargs):
        return {}

    def request_as_comment_dict(self, request):
        return {
            'who': request.creator,
            'when': date_parse(request.created_at),
            'id': '-1',
            'parent': None,
            'comment': request.description,
        }

    def comment_find(self, comments, bot, info_match=None):
        return None, None

    def command_find(self, comments, user, command, who_allowed):
        if False:
            yield


class RequestAction:
    """Stub action structure for running as an Gitea Action"""
    def __init__(
            self,
            src_project,
            src_package,
            src_rev,
            dst_project,
            dst_package,
    ):
        self.type = "submit"  # XXX is there any other types when running as an action?
        self.src_project = src_project
        self.src_package = src_package
        self.src_rev = src_rev
        self.tgt_project = dst_project
        self.tgt_package = dst_package


class Request:
    """Stub request structure for running as an Gitea Action"""
    def __init__(self):
        src_full_name = os.environ["PR_SRC_FULL_NAME"]
        src_project, src_package = src_full_name.split('/', 1)
        src_rev = os.environ["PR_SRC_REV"]
        dst_full_name = os.environ["PR_DST_FULL_NAME"]
        dst_project, dst_package = dst_full_name.split('/', 1)
        creator = os.environ["PR_CREATOR"]
        created_at = os.environ["PR_CREATED_AT"]
        description = os.environ["PR_DESCRIPTION"]

        self.reqid = '1'
        self.actions = [RequestAction(f"head:{src_project}", src_package, src_rev, f"base:{dst_project}", dst_package)]
        self.creator = creator
        self.created_at = created_at
        self.description = description
        self.reviews = []


class StubProjectConfig:
    """Stub project config loader"""
    def get(self, _key, default=None):
        return default


class Action(plat.base.PlatformBase):
    """Platform interface implementation for running as Gitea Actions"""
    def __init__(self, logger):
        self.logger = logger
        self.comment_api = CommentAPI()

    @staticmethod
    def get_stub_request():
        return Request()

    @property
    def name(self) -> str:
        return "ACTION"

    def get_request(self, request_id, with_full_history=False):
        # thanks to duck-typing we can return a stub request struct
        return Action.get_stub_request()

    def get_project_config(self, project):
        return StubProjectConfig()

    def get_request_age(self, request):
        raise NotImplementedError("get_request_age not implemented for actions")

    def get_request_list_with_history(
            self, project='', package='', req_who='', req_state=('new', 'review', 'declined'),
            req_type=None, exclude_target_projects=[]):
        raise NotImplementedError("get_request_list_with_history not implemented for actions")

    def get_staging_api(self, project):
        raise NotImplementedError("get_staging_api not implemented for actions")

    def search_review(self, **kwargs):
        raise NotImplementedError("search_review not implemented for actions")

    def can_accept_review(self, req, **kwargs):
        raise NotImplementedError("can_accept_review not implemented for actions")

    def change_review_state(self, req, newstate, message, **kwargs):
        raise NotImplementedError("change_review_state not implemented for actions")
