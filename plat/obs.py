import plat.base

from lxml import etree as ET

from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.core import get_request_list_with_history, request_age
from osclib.stagingapi import StagingAPI
import osc.core
from urllib.error import HTTPError


class OBS(plat.base.PlatformBase):
    """Implementation of platform interface for OBS"""

    def __init__(self, apiurl) -> None:
        self.apiurl = apiurl
        self.comment_api = CommentAPI(self.apiurl)

    def _get(self, path_list, query=None):
        """Construct a complete URL, and issue an HTTP GET to it."""
        url = osc.core.makeurl(self.apiurl, path_list, query)
        return osc.core.http_GET(url)

    @property
    def name(self) -> str:
        return "OBS"

    def get_request(self, request_id, with_full_history=False):
        query = {'withfullhistory': '1'} if with_full_history else None
        res = self._get(('request', request_id), query)
        root = ET.parse(res).getroot()
        req = osc.core.Request()
        req.read(root)
        return req

    def get_project_config(self, project):
        return Config.get(self.apiurl, project)

    def get_request_age(self, request):
        # XXX we might want to reconsider whether this belongs there
        return request_age(request)

    def get_request_list_with_history(
            self, project='', package='', req_who='', req_state=('new', 'review', 'declined'),
            req_type=None, exclude_target_projects=[]):
        """Get requests with full history"""
        return get_request_list_with_history(project, package, req_who, req_state, req_type,
                                             exclude_target_projects)

    def get_staging_api(self, project):
        return StagingAPI(self.apiurl, project)

    def search_review(self, **kwargs):
        review_user = kwargs.get("review_user")
        review_group = kwargs.get("review_group")
        review = None
        if review_user:
            review = f"@by_user='{review_user}' and @state='new'"
        if review_group:
            review = osc.core.xpath_join(review, f"@by_group='{review_group}' and @state='new'")
        url = osc.core.makeurl(self.apiurl, ('search', 'request'), {
                               'match': f"state/@name='review' and review[{review}]", 'withfullhistory': 1})
        root = ET.parse(osc.core.http_GET(url)).getroot()

        ret = []

        for request in root.findall('request'):
            req = osc.core.Request()
            req.read(request)
            ret.append(req)

        return ret

    def _has_open_review_by(self, root, by_what, reviewer):
        states = set([review.get('state') for review in root.findall('review') if review.get(by_what) == reviewer])
        if not states:
            return None
        elif 'new' in states:
            return True
        return False

    def can_accept_review(self, req, **kwargs):
        review_user = kwargs.get("review_user")
        review_group = kwargs.get("review_group")
        url = osc.core.makeurl(self.apiurl, ('request', str(req.reqid)))
        try:
            root = ET.parse(osc.core.http_GET(url)).getroot()
            if review_user and self._has_open_review_by(root, 'by_user', review_user):
                return True
            if review_group and self._has_open_review_by(root, 'by_group', review_group):
                return True
        except HTTPError as e:
            print(f'ERROR in URL {url} [{e}]')
        return False

    def change_review_state(self, req, newstate, message, **kwargs):
        return osc.core.change_review_state(apiurl=self.apiurl,
                                            reqid=req.reqid, newstate=newstate,
                                            message=message, **kwargs)
