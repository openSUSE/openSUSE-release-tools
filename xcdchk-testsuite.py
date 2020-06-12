#!/usr/bin/python2

import os
import sys

import logging
import ToolBase

from xdg.BaseDirectory import load_first_config
from lxml import etree as ET

logger = logging.getLogger()

JUMP="openSUSE:Jump:15.2"

class XCDCHKTestSuite(ToolBase.ToolBase):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option('-p', '--project', dest='project', metavar='PROJECT',
                        help='project to process (default: %s)' % JUMP,
                        default = JUMP)
        return parser

    def setup_tool(self):
        tool = XCDCHKTestSuite()
        tool.project = self.options.project
        return tool

if __name__ == "__main__":
    app = XCDCHKTestSuite()
    sys.exit( app.main() )
