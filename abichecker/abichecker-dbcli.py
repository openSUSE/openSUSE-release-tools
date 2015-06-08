#!/usr/bin/python
# Copyright (c) 2015 SUSE Linux GmbH
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from pprint import pprint
import os, sys, re
import logging
import cmdln

import abichecker_dbmodel as DB
from abichecker_common import Config

class BoilderPlate(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, args, kwargs)
        self.session = None

    def get_optparser(self):
        parser = cmdln.CmdlnOptionParser(self)
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("--debug", action="store_true", help="debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")
        return parser

    def postoptparse(self):
        logging.basicConfig()
        self.logger = logging.getLogger(self.optparser.prog)
        if (self.options.debug):
            self.logger.setLevel(logging.DEBUG)
        elif (self.options.verbose):
            self.logger.setLevel(logging.INFO)

        DB.Base.metadata.create_all(DB.db_engine())
        self.session = DB.db_session()

    def do_list(self, subcmd, opts, *args):
        """${cmd_name}: foo bar

        ${cmd_usage}
        ${cmd_option_list}
        """

        for r in self.session.query(DB.Request).all():
            print('%s %s'%(r.id, r.state))
            for a in r.abichecks:
                print('  %s %s %s'%(a.dst_project, a.dst_package, a.result))
                for r in a.reports:
                    print('    %s %10s %-25s %s'%(r.id, r.arch, r.dst_lib, r.result))

    def do_log(self, subcmd, opts, request_id):
        """${cmd_name}: foo bar

        ${cmd_usage}
        ${cmd_option_list}
        """

        request = self.session.query(DB.Request).filter(DB.Request.id == request_id).one()
        for log in request.log:
            print log.line

    def do_delete(self, subcmd, opts, request_id):
        """${cmd_name}: foo bar

        ${cmd_usage}
        ${cmd_option_list}
        """

        request = self.session.query(DB.Request).filter(DB.Request.id == request_id).one()
        self.session.delete(request)
        self.session.commit()

    def do_recheck(self, subcmd, opts, request_id):
        """${cmd_name}: set request id to seen

        ${cmd_usage}
        ${cmd_option_list}
        """

        request = self.session.query(DB.Request).filter(DB.Request.id == request_id).one()
        logentry = DB.Log(request_id = request_id,
            line = 'manually setting state to seen. previous state: %s (%s)'%(request.state, request.result))
        request.state = 'seen'
        request.result = None
        self.session.add(logentry)
        self.session.commit()

    @cmdln.option("--get", action="store_true", help="get some values")
    @cmdln.option("--set", action="store_true", help="set some values")
    @cmdln.option("--delete", action="store_true", help="delete some values")
    def do_config(self, subcmd, opts, *args):
        """${cmd_name}: manage config file

        ${cmd_usage}
        ${cmd_option_list}
        """

        config = Config(self.session)
        if opts.set:
            config.set(args[0], args[1])
        elif opts.get:
            print config.get(args[0])
        elif opts.delete:
            config.delete(args[0])
        else:
            for entry in config.settings():
                print "%s=%s"%entry

if __name__ == "__main__":
    app = BoilderPlate()
    sys.exit( app.main() )

# vim: sw=4 et
