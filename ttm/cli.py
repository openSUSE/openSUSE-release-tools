# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import logging
import ToolBase
import cmdln

from ttm.manager import ToTestManager, QAResult
from ttm.releaser import ToTestReleaser
from ttm.publisher import ToTestPublisher

logger = logging.getLogger()

class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    @cmdln.option('--force', action='store_true', help="Just publish, don't check")
    def do_publish(self, subcmd, opts, project):
        """${cmd_name}: check and publish ToTest

        ${cmd_usage}
        ${cmd_option_list}
        """

        if ToTestPublisher(self.tool).publish(project, opts.force) == QAResult.failed:
            return 1

    @cmdln.option('--force', action='store_true', help="Just update status")
    def do_wait_for_published(self, subcmd, opts, project):
        """${cmd_name}: wait for ToTest to contain publishing status and publisher finished

        ${cmd_usage}
        ${cmd_option_list}
        """

        ToTestPublisher(self.tool).wait_for_published(project, opts.force)

    @cmdln.option('--force', action='store_true', help="Just release, don't check")
    def do_release(self, subcmd, opts, project):
        """${cmd_name}: check and release from project to ToTest

        ${cmd_usage}
        ${cmd_option_list}
        """

        if ToTestReleaser(self.tool).release(project, opts.force) == QAResult.failed:
            return 1

    def do_run(self, subcmd, opts, project):
        """${cmd_name}: run the ToTest Manager

        ${cmd_usage}
        ${cmd_option_list}
        """

        if ToTestPublisher(self.tool).publish(project) == QAResult.passed:
            ToTestPublisher(self.tool).wait_for_published(project)
        ToTestReleaser(self.tool).release(project)

    @cmdln.option('--status', help='The status to fix (e.g. testing,publishing)')
    @cmdln.option('--snapshot', help='Version to update to')
    def do_fixup(self, subcmd, opts, project):
        """${cmd_name}: fixup TTM status - use in case of emergency

        ${cmd_usage}
        ${cmd_option_list}
        """
        manager = ToTestManager(self.tool)
        manager.setup(project)
        manager.update_status(opts.status, opts.snapshot)
