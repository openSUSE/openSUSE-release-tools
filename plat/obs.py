import plat.base

from lxml import etree as ET

from osclib.comments import CommentAPI
import osc.core

class OBS(plat.base.PlatformBase):
    """Implementation of platform interface for OBS"""

    def __init__(self, apiurl) -> None:
        self.apiurl = apiurl
        self.comment_api = CommentAPI(self.apiurl)

    def _get(self, l, query=None):
        """Construct a complete URL, and issue an HTTP GET to it."""
        url = osc.core.makeurl(self.apiurl, l, query)
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
