#!/usr/bin/python

from __future__ import print_function

from xml.etree import cElementTree as ET
import cmdln
import datetime
import itertools
import logging
import signal
import sys
import time

try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

import osc.conf
import osc.core
from urllib import quote_plus

from osclib.memoize import memoize

logger = logging.getLogger()

http_GET = osc.core.http_GET
http_DELETE = osc.core.http_DELETE
http_POST = osc.core.http_POST

# http://stackoverflow.com/questions/312443/how-do-you-split-a-list-into-evenly-sized-chunks-in-python
def chunks(l, n):
    """ Yield successive n-sized chunks from l.
    """
    for i in xrange(0, len(l), n):
        yield l[i:i+n]

class ToolBase(object):
    def __init__(self):
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        self.caching = False
        self.dryrun = False

    @memoize(add_invalidate=True)
    def _cached_GET(self, url):
        return self.retried_GET(url).read()

    def cached_GET(self, url):
        if self.caching:
            return self._cached_GET(url)
        return self.retried_GET(url).read()

    def retried_GET(self, url):
        try:
            return http_GET(url)
        except HTTPError as e:
            if 500 <= e.code <= 599:
                print('Retrying {}'.format(url))
                time.sleep(1)
                return self.retried_GET(url)
            logging.error('%s: %s', e, url)
            raise e

    def http_PUT(self, *args, **kwargs):
        if self.dryrun:
            logging.debug("dryrun PUT %s %s", args, str(kwargs)[:200])
        else:
            osc.core.http_PUT(*args, **kwargs)

    def http_POST(self, *args, **kwargs):
        if self.dryrun:
            logging.debug("dryrun POST %s %s", args, str(kwargs)[:200])
        else:
            osc.core.http_POST(*args, **kwargs)

    def get_project_meta(self, prj):
        url = self.makeurl(['source', prj, '_meta'])
        return self.cached_GET(url)

    def _meta_get_packagelist(self, prj, deleted=None, expand=False):

        query = {}
        if deleted:
            query['deleted'] = 1
        if expand:
            query['expand'] = 1

        u = self.makeurl(['source', prj], query)
        return self.cached_GET(u)

    def meta_get_packagelist(self, prj, deleted=None, expand=False):
        root = ET.fromstring(self._meta_get_packagelist(prj, deleted, expand))
        return [ node.get('name') for node in root.findall('entry') if not node.get('name') == '000product' and not node.get('name').startswith('patchinfo.') ]

    # FIXME: duplicated from manager_42
    def latest_packages(self, project):
        data = self.cached_GET(self.makeurl(['project', 'latest_commits', project]))
        lc = ET.fromstring(data)
        packages = set()
        for entry in lc.findall('{http://www.w3.org/2005/Atom}entry'):
            title = entry.find('{http://www.w3.org/2005/Atom}title').text
            if title.startswith('In '):
                packages.add(title[3:].split(' ')[0])
        return sorted(packages)

    def makeurl(self, l, query=None):
        """
        Wrapper around osc's makeurl passing our apiurl
        :return url made for l and query
        """
        query = [] if not query else query
        return osc.core.makeurl(self.apiurl, l, query)


    def process(self, packages):
        """ reimplement this """
        True

class CommandLineInterface(cmdln.Cmdln):
    def __init__(self, *args, **kwargs):
        cmdln.Cmdln.__init__(self, *args, **kwargs)

    def get_optparser(self):
        parser = cmdln.Cmdln.get_optparser(self)
        parser.add_option("--apiurl", '-A', metavar="URL", help="api url")
        parser.add_option("--dry", action="store_true", help="dry run")
        parser.add_option("-d", "--debug", action="store_true", help="debug output")
        parser.add_option("--osc-debug", action="store_true", help="osc debug output")
        parser.add_option("--verbose", action="store_true", help="verbose")

        parser.add_option('--cache-requests', action='store_true', default=False,
                        help='cache GET requests. Not recommended for daily use.')

        return parser

    def postoptparse(self):
        level = None
        if (self.options.debug):
            level = logging.DEBUG
        elif (self.options.verbose):
            level = logging.INFO

        logging.basicConfig(level=level)

        osc.conf.get_config(override_apiurl = self.options.apiurl)

        if self.options.osc_debug:
            osc.conf.config['debug'] = 1

        self.tool = self.setup_tool()
        self.tool.dryrun = self.options.dry
        self.tool.caching = self.options.cache_requests

    def setup_tool(self, toolclass = ToolBase):
        """ reimplement this """

        tool = toolclass()

        return tool

# example
#    @cmdln.option('-n', '--interval', metavar="minutes", type="int", help="periodic interval in minutes")
#    def do_process(self, subcmd, opts, project):
#        def work():
#            self.tool.process()
#
#        self.runner(work, opts.interval)

    def runner(self, workfunc, interval):
        """ runs the specified callback every <interval> minutes or
        once if interval is None or 0
        """
        class ExTimeout(Exception):
            """raised on timeout"""

        if interval:
            def alarm_called(nr, frame):
                raise ExTimeout()
            signal.signal(signal.SIGALRM, alarm_called)

        while True:
            try:
                workfunc()
            except Exception as e:
                logger.exception(e)

            if interval:
                logger.info("sleeping %d minutes. Press enter to check now ..."%interval)
                signal.alarm(interval*60)
                try:
                    raw_input()
                except ExTimeout:
                    pass
                signal.alarm(0)
                logger.info("recheck at %s"%datetime.datetime.now().isoformat())
                continue
            break

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )
