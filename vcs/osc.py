import vcs.base

import os
import sys
import shutil
import osc.core
from urllib.error import HTTPError


class OSC(vcs.base.VCSBase):
    """VCS interface implementation for OSC"""

    def __init__(self, apiurl: str):
        self.apiurl = apiurl

    @property
    def name(self) -> str:
        return "OSC"

    def _get(self, path_list, query=None):
        """Construct a complete URL, and issue an HTTP GET to it."""
        url = osc.core.makeurl(self.apiurl, path_list, query)
        return osc.core.http_GET(url)

    def get_path(self, *args):
        try:
            return self._get(args)
        except HTTPError as e:
            if e.code != 404:
                raise e
            return None

    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        _stdout = sys.stdout
        sys.stdout = open(os.devnull, 'w')
        try:
            result = osc.core.checkout_package(
                self.apiurl,
                target_project,
                target_package,
                pathname=pathname,
                **kwargs
            )
            shutil.rmtree(os.path.join(target_package, '.osc'))
        finally:
            sys.stdout = _stdout
        return result
