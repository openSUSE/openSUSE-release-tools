import logging
import ToolBase

logger = logging.getLogger()

class XCDCHK(ToolBase.ToolBase):
    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.logger = logging.getLogger(__name__)
