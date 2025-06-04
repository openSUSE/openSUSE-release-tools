import plat.base
import os
import requests
import base64
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

    def request(self, method, path, **kwargs):
        arg_headers = kwargs.get('headers') or {}
        headers = {'Authorization': f'token {self.token}'}
        headers.update(arg_headers)
        kwargs['headers'] = headers

        url = urljoin(self.base_url, path)
        return requests.request(method, url, **kwargs)

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

    def get_comments(self, request, project_name=None, package_name=None):
        project_name = project_name or request._owner
        package_name = package_name or request._repo
        res = self.api.get(f'repos/{project_name}/{package_name}/issues/{request.reqid}/comments')
        res.raise_for_status()

        json = res.json()
        comments = {}
        for c in json:
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
                        key, value = pair.split('=')
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
        res = self.api.post(f'repos/{project_name}/{package_name}/issues/{request_id}/comments',
                            json={"body": comment})
        res.raise_for_status()

    def delete(self, comment_id, project, package, request):
        self.api.delete(f'repos/{project}/{package}/issues/{request}/comments/{comment_id}')

class StagingAPI:
    """StagingAPI implementation for Gitea"""

    def __init__(self, project, api):
        self.project = project
        self.api = api

    def pseudometa_file_load(self, filename):
        res = self.api.get(f'repos/{self.project}/_meta/{filename}')
        res.raise_for_status()
        data = res.json()
        content = base64.b64decode(data["content"]).decode("utf-8")
        print(content)

class RequestAction:
    """Stub action class"""
    def __init__(self, type, src_project, src_package, src_rev, tgt_project, tgt_package):
        self.type = type
        self.src_project = src_project
        self.src_package = src_package
        self.src_rev = src_rev
        self.tgt_project = tgt_project
        self.tgt_package = tgt_package

class Request:
    """Request structure implemented for Gitea"""
    def __init__(self):
        self._init_attributes()

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
        self.owner = None
        self.repo = None

    def read(self, json, owner, repo):
        """Read in a request from JSON response"""
        self._init_attributes()
        self.reqid = str(json["number"])
        self.creator = json["user"]["login"]
        self.created_at = json["created_at"]
        self.title = json["title"]
        self.description = json["body"]
        self.state = json["state"]
        self.actions=[RequestAction(
            type="submit",
            src_project=json["head"]["repo"]["owner"]["login"],
            src_package=json["head"]["repo"]["name"],
            src_rev=json["head"]["sha"],
            tgt_project=json["base"]["repo"]["owner"]["login"],
            tgt_package=json["base"]["repo"]["name"])]
        if json.get("merged"):
            self.accept_at = json["merged_at"]

        self._owner = owner
        self._repo = repo


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

    def get_request(self, request_id, with_full_history=False):
        return Request()

    def get_project_config(self, project):
        return ProjectConfig()

    def get_request_age(self, request):
        return datetime.now(timezone.utc) - datetime.fromisoformat(request.created_at)

    def _request_from_issue(self, issue_json):
        owner = issue_json["repository"]["owner"]
        repo = issue_json["repository"]["name"]
        pr_id = issue_json["number"]
        res = self.api.get(f'repos/{owner}/{repo}/pulls/{pr_id}')
        res.raise_for_status()

        ret = Request()
        ret.read(res.json(), owner=owner, repo=repo)
        return ret

    def get_request_list_with_history(
            self, project='', package='', req_who='', req_state=('new', 'review', 'declined'),
            req_type=None, exclude_target_projects=[]):
        if package != '':
            repos = [package]
        else:
            repo_list_res = self.api.get(f'orgs/{project}/repos')
            repo_list_res.raise_for_status()
            repo_list_json = repo_list_res.json()
            repos = [i["name"] for i in repo_list_json]

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
            search_res = self.api.get(f'repos/issues/search', params=params)
            search_res.raise_for_status()
            search_json = search_res.json()
            if not search_json:
                break

            for i in search_json:
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

        res = self.api.post(f'repos/{req._owner}/{req._repo}/pulls/{req.reqid}/reviews', json=json)
        res.raise_for_status()
