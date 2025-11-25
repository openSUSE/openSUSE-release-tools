import scm.base

import os
import sys
import shutil
import osc.core


class OSC(scm.base.SCMBase):
    """SCM interface implementation for OSC"""

    def __init__(self, apiurl: str):
        self.apiurl = apiurl

    @property
    def name(self) -> str:
        return "OSC"

    def checkout_package(
            self,
            target_project: str,
            target_package: str,
            pathname,
            **kwargs
    ):
        with open(os.devnull, 'w') as devnull:
            _stdout = sys.stdout
            sys.stdout = devnull
            try:
                result = osc.core.checkout_package(
                    self.apiurl,
                    target_project,
                    target_package,
                    pathname=pathname,
                    **kwargs
                )
                shutil.rmtree(os.path.join(pathname, target_package, '.osc'))
            finally:
                sys.stdout = _stdout
            return result
