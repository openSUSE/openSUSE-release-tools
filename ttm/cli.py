#!/usr/bin/python2
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

from __future__ import print_function

import logging
import ToolBase

from ttm.manager import ToTestManager

logger = logging.getLogger()

class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def setup_tool(self):
        tool = ToTestManager()
        if self.options.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.options.verbose:
            logging.basicConfig(level=logging.INFO)

        return tool

    def do_run(self, subcmd, opts, project):
        """${cmd_name}: run the ToTest Manager

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.totest(project)

    def do_release(self, subcmd, opts, project='openSUSE:Factory'):
        """${cmd_name}: manually release all media. Use with caution!

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.release(project)
