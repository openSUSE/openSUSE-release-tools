import plat.base
import os
import requests
import html
import re
from datetime import datetime, timezone
from urllib.parse import urljoin


class API:
    """Gitea API wrapper class"""

    @staticmethod
    def get_token():
        ret = os.environ.get("GITEA_ACCESS_TOKEN")
        if ret is None:
            raise RuntimeError("GITEA_ACCESS_TOKEN is not set. You need to set an access token for Gitea interaction to work.")
        return ret

    def __init__(self, url):
        self.base_url = urljoin(url, "/api/v1/")
        self.token = self.get_token()

    def request(self, method, path, raise_for_status=True, **kwargs):
        arg_headers = kwargs.get('headers') or {}
        headers = {'Authorization': f'token {self.token}'}
        headers.update(arg_headers)
        kwargs['headers'] = headers

        url = urljoin(self.base_url, path)
        ret = requests.request(method, url, **kwargs)
        if raise_for_status:
            ret.raise_for_status()
        return ret

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, **kwargs):
        return self.request('POST', path, **kwargs)

    def delete(self, path, **kwargs):
        return self.request('DELETE', path, **kwargs)


class CommentAPI:
    """CommentAPI implementation for Gitea"""
    COMMENT_MARKER_REGEX = re.compile(r'<!-- (?P<bot>[^ ]+)(?P<info>(?: [^= ]+=[^ ]+)*) -->')

    def __init__(self, api):
        self.api = api

    def _comment_as_dict(self, comment):
        return {
            'who': comment["user"]["login"],
            'when': datetime.fromisoformat(comment["created_at"]),
            'id': comment["id"],
            'parent': None,
            'comment': html.unescape(comment["body"])
        }

    def get_comments(self, request_id, project_name=None, package_name=None):
        project_name, package_name, pr_id = Request.parse_request_id(request_id)
        res = self.api.get(f'repos/{project_name}/{package_name}/issues/{pr_id}/comments').json()
        comments = {}
        for c in res:
            c = self._comment_as_dict(c)
            comments[c['id']] = c
        return comments

    def request_as_comment_dict(self, request):
        return {
            'who': request.creator,
            'when': datetime.fromisoformat(request.created_at),
            'id': '-1',
            'parent': None,
            'comment': request.description,
        }

    def comment_find(self, comments, bot, info_match=None):
        # XXX deduplicate this?

        # Case-insensitive for backwards compatibility.
        bot = bot.lower()
        for c in comments.values():
            m = self.COMMENT_MARKER_REGEX.match(c['comment'])
            if m and bot == m.group('bot').lower():
                info = {}

                # Python base regex does not support repeated subgroup capture
                # so parse the optional info using string split.
                stripped = m.group('info').strip()
                if stripped:
                    for pair in stripped.split(' '):
                        key, value = pair.split('=', 1)
                        info[key] = value

                # Skip if info does not match.
                if info_match:
                    match = True
                    for key, value in info_match.items():
                        if not (value is None or (key in info and info[key] == value)):
                            match = False
                            break
                    if not match:
                        continue

                return c, info
        return None, None

    def command_find(self, comments, user, command, who_allowed):
        # TODO
        if False:
            yield

    def add_marker(self, comment, bot, info=None):
        """Add bot marker to comment that can be used to find comment."""

        # TODO deduplicate this?
        infos = []
        if info:
            for key, value in info.items():
                infos.append('='.join((str(key), str(value))))

        marker = f"<!-- {bot}{' ' + ' '.join(infos) if info else ''} -->"
        return marker + '\n\n' + comment

    def add_comment(self, request_id=None, project_name=None,
                    package_name=None, comment=None, _parent_id=None):
        self.api.post(f'repos/{project_name}/{package_name}/issues/{request_id}/comments',
                      json={"body": comment})

    def delete(self, comment_id, project, package, request):
        self.api.delete(f'repos/{project}/{package}/issues/{request}/comments/{comment_id}',
                        raise_for_status=False)


class StagingAPI:
    """StagingAPI implementation for Gitea"""
    # XXX bare minimal stub. To be implemented on-demand

    def __init__(self, project, api):
        self.project = project
        self.api = api


class RequestAction:
    """Stub action class"""
    def _set_attr_from_json(self, attr, json, path):
        node = json
        for i in path.split('.'):
            node = node.get(i)
            if node is None:
                return

        setattr(self, attr, node)

    def __init__(self, type, json):
        self.type = type
        self._set_attr_from_json('src_project', json, 'head.repo.owner.login')
        self._set_attr_from_json('src_package', json, 'head.repo.name')
        self._set_attr_from_json('src_branch', json, 'head.ref')
        self._set_attr_from_json('src_rev', json, 'head.sha')
        self._set_attr_from_json('tgt_project', json, 'base.repo.owner.login')
        self._set_attr_from_json('tgt_package', json, 'base.repo.name')
        self._set_attr_from_json('tgt_branch', json, 'base.ref')
        self._set_attr_from_json('tgt_rev', json, 'base.sha')


class Review:
    attributes = ["by", "state", "type", "when"]

    states_mapping = {
        "APPROVED": "accepted",
        "REQUEST_CHANGES": "declined",
        "REQUEST_REVIEW": "new",
    }

    def __init__(self, **kwargs):
        self._review_data = kwargs

    def __getattr__(self, attribute):
        if attribute == "state":
            return self.states_mapping[self._review_data.get("state", "REQUEST_REVIEW")]

        return self._review_data.get(attribute, None)


class Request:
    """Request structure implemented for Gitea"""
    def __init__(self):
        self._init_attributes()

    @staticmethod
    def parse_request_id(reqid):
        owner, repo, pr_id = reqid.split(':')
        return owner, repo, pr_id

    @staticmethod
    def construct_request_id(owner, repo, pr_id):
        return f'{owner}:{repo}:{pr_id}'

    @staticmethod
    def format_review(review):
        if review.get("user") is not None:
            return Review(
                by=review["user"]["login"],
                state=review["state"],
                type="User",
                when=review["updated_at"]
            )
        elif review.get("team") is not None:
            return Review(
                by=review["team"]["name"],
                state=review["state"],
                type="Group",
                when=review["updated_at"]
            )
        else:
            raise Exception("Unknown review type")

    def _init_attributes(self):
        self.reqid = None
        self.creator = ''
        self.created_at = ''
        self.title = ''
        self.description = ''
        self.priority = None
        self.state = None
        self.accept_at = None
        self.actions = []
        self.statehistory = []
        self.reviews = []
        self._issues = None

        # Gitea specific attributes
        self._owner = None
        self._repo = None
        self._pr_id = None

    def read(self, request_json, reviews_json, owner, repo):
        """Read in a request from JSON response"""
        self._init_attributes()

        self._owner = owner
        self._repo = repo
        self._pr_id = request_json["number"]

        self.reqid = Request.construct_request_id(owner, repo, request_json["number"])
        self.creator = request_json["user"]["login"]
        self.created_at = request_json["created_at"]
        self.updated_at = request_json["updated_at"]
        self.title = request_json["title"]
        self.description = request_json["body"]
        self.state = request_json["state"]

        self.actions = [RequestAction(type="submit", json=request_json)]

        if request_json.get("merged"):
            self.accept_at = request_json["merged_at"]

        for review in reviews_json:
            if not review.get("dismissed", False):
                self.reviews.append(Request.format_review(review))

class ProjectConfig:
    """Project Config implemented for Gitea"""
    def get(self, _key, default=None):
        # TODO
        return default


class Gitea(plat.base.PlatformBase):
    """Platform interface implementation for Gitea"""
    def __init__(self, logger, url):
        self.logger = logger
        self.url = url
        self.api = API(self.url)

        self.comment_api = CommentAPI(self.api)

    @property
    def name(self) -> str:
        return "GITEA"

    def get_path(self, *args):
        path = '/'.join(args)
        return self.api.get(path)

    def _get_request(self, pr_id, owner, repo):
        res = self.api.get(f'repos/{owner}/{repo}/pulls/{pr_id}').json()

        reviews = self.api.get(f'repos/{owner}/{repo}/pulls/{pr_id}/reviews').json()

        ret = Request()
        ret.read(res, reviews, owner=owner, repo=repo)
        return ret

    def get_request(self, request_id, with_full_history=False):
        owner, repo, pr_id = Request.parse_request_id(request_id)
        return self._get_request(pr_id, owner, repo)

    def get_project_config(self, project):
        return ProjectConfig()

    def get_request_age(self, request):
        return datetime.now(timezone.utc) - datetime.fromisoformat(request.created_at)

    def _request_from_issue(self, issue_json):
        owner = issue_json["repository"]["owner"]
        repo = issue_json["repository"]["name"]
        pr_id = issue_json["number"]
        return self._get_request(pr_id, owner, repo)

    def get_request_list_with_history(
            self, project='', package='', req_who='', req_state=('new', 'review', 'declined'),
            req_type=None, exclude_target_projects=[]):
        if package != '':
            repos = [package]
        else:
            repo_list = self.api.get(f'orgs/{project}/repos').json()
            repos = [i["name"] for i in repo_list]

        for r in repos:
            list_res = self.api.get(f'repos/{project}/{r}/issues?type=pulls')
            list_res.raise_for_status()
            list_json = list_res.json()

            for i in list_json:
                yield self._request_from_issue(i)

    def get_staging_api(self, project):
        return StagingAPI(project, self.api)

    def search_review(self, **kwargs):
        params: dict[str, str | int] = {'state': 'open', "type": "pulls", "review_requested": "true"}

        page = 1
        while True:
            params["page"] = page
            page += 1
            search_res = self.api.get('repos/issues/search', params=params).json()
            if not search_res:
                break

            for i in search_res:
                yield self._request_from_issue(i)

    def can_accept_review(self, req, **kwargs):
        # stub
        return True

    def change_review_state(self, req, newstate, message, **kwargs):
        json = {'body': message}
        if newstate == 'accepted':
            json['event'] = 'APPROVED'
        elif newstate == 'declined':
            json['event'] = 'REQUEST_CHANGES'

        self.api.post(f'repos/{req._owner}/{req._repo}/pulls/{req._pr_id}/reviews', json=json)
