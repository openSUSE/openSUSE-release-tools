import vcs.base

import osc.core
from urllib.error import HTTPError, URLError

class OSC(vcs.base.VCSBase):
    """VCS interface implementation for OSC"""

    def __init__(self, apiurl=None):
        self.apiurl = apiurl

    @property
    def name(self) -> str:
        return "osc"

    def _get(self, l, query=None):
        """Construct a complete URL, and issue an HTTP GET to it."""
        url = osc.core.makeurl(self.apiurl, l, query)
        return osc.core.http_GET(url)

    def get_path(self, *args):
        try:
            return self._get(args)
        except HTTPError as e:
            if e.code != 404:
                raise e
            return None
