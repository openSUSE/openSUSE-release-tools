import vcs.base

class OSC(vcs.base.VCSBase):
    """VCS interface implementation for OSC"""

    def __init__(self, apiurl=None):
        self.apiurl = apiurl

    @property
    def name(self) -> str:
        return "osc"
