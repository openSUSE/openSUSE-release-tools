#!/usr/bin/python3

from pprint import pprint
import os, sys, re
import logging
import cmdln

import abichecker_dbmodel as DB
from abichecker_common import Config, CACHEDIR
from datetime import datetime, timedelta

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

        for req in self.session.query(DB.Request).all():
            print(f'{req.id} {req.state}')
            for a in req.abichecks:
                print(f'  {a.dst_project} {a.dst_package} {a.result}')
                for r in a.reports:
                    print('    %s %10s %-25s %s'%(r.id, r.arch, r.dst_lib, r.result))

    def do_prune(self, subcmd, opts, days):
        """${cmd_name}: prune old records

        ${cmd_usage}
        ${cmd_option_list}
        """

        oldest = datetime.today() - timedelta(days = 365)

        requests = self.session.query(DB.Request).filter(DB.Request.t_updated < oldest)
        for req in requests:
            for a in req.abichecks:
                self.logger.info('prune  %s %s %s', req.id, a.dst_project, a.dst_package)
                for r in a.reports:
                    fn = os.path.join(CACHEDIR, r.htmlreport)
                    if os.path.exists(fn):
                        self.logger.info('removing %s', r.htmlreport)
                        os.unlink(fn)
            self.session.delete(req)
        self.session.commit()

    def do_log(self, subcmd, opts, request_id):
        """${cmd_name}: foo bar

        ${cmd_usage}
        ${cmd_option_list}
        """

        request = self.session.query(DB.Request).filter(DB.Request.id == request_id).one()
        for log in request.log:
            print(log.line)

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
            line = f'manually setting state to seen. previous state: {request.state} ({request.result})')
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
            print(config.get(args[0]))
        elif opts.delete:
            config.delete(args[0])
        else:
            for entry in config.settings():
                print("%s=%s"%entry)

if __name__ == "__main__":
    app = BoilderPlate()
    sys.exit( app.main() )

