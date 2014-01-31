#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc checker --help' to see the usage.


import socket
import os
import traceback
import subprocess
import urllib2


def do_check_core(self, subcmd, opts, *args):
    """${cmd_name}: check_core review of submit requests.

    Usage:
      osc check_core [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    if len(args) == 0:
        raise oscerr.WrongArgs("Please give a subcommand to 'osc checkcore' or try 'osc help checkcore'")

    opts.verbose = False

    from pprint import pprint

    opts.apiurl = self.get_api_url()

    tmphome = None

    self.letter_to_accept = None
    if args[0] == 'accept':
        self.letter_to_accept = args[1]
    elif args[0] == 'freeze':

        return # don't

    # xpath query, using the -m, -r, -s options
    where = "@by_group='factory-staging'+and+@state='new'"

    url = makeurl(opts.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
    f = http_GET(url)
    root = ET.parse(f).getroot()
    for rq in root.findall('request'):
        tprj = rq.find('action/target').get('project')
        self._checkercore_one_request(rq, opts)

    if self.letter_to_accept:
        url = makeurl(opts.apiurl, ['source', 'openSUSE:Factory:Staging:%s' % self.letter_to_accept])
        f = http_GET(url)
        root = ET.parse(f).getroot()
        print ET.tostring(root)

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
