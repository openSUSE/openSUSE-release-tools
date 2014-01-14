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

def _checkercore_change_review_state(self, opts, id, newstate, by_group='', by_user='', message='', supersed=None):
    """ taken from osc/osc/core.py, improved:
        - verbose option added,
        - empty by_user=& removed.
        - numeric id can be int().
    """
    query = {'cmd': 'changereviewstate', 'newstate': newstate }
    if by_group:  query['by_group'] = by_group
    if by_user:   query['by_user'] = by_user
    if supersed: query['superseded_by'] = supersed
#    if message: query['comment'] = message
    u = makeurl(opts.apiurl, ['request', str(id)], query=query)
    f = http_POST(u, data=message)
    root = ET.parse(f).getroot()
    return root.attrib['code']

def _checkercore_get_rings(self, opts):
    ret = dict()
    for prj in ['openSUSE:Factory:Build', 'openSUSE:Factory:Core', 'openSUSE:Factory:MainDesktops', 'openSUSE:Factory:DVD']:
        u = makeurl(opts.apiurl, ['source', prj])
        f = http_GET(u)
        for entry in ET.parse(f).getroot().findall('entry'):
            ret[entry.attrib['name']] = prj
    return ret

def _checkercore_one_request(self, rq, cmd, opts):
    if (opts.verbose):
        ET.dump(rq)
        print(opts)
    id = int(rq.get('id'))
    act_id = 0
    approved_actions = 0
    actions = rq.findall('action')
    act = actions[0]
        
    tprj = act.find('target').get('project')
    tpkg = act.find('target').get('package')

    e = []
    if not tpkg:
        e.append('no target/package in request %d, action %d; ' % (id, act_id))
    if not tprj:
        e.append('no target/project in request %d, action %d; ' % (id, act_id))
    # it is no error, if the target package dies not exist

    ring = self.rings.get(tpkg, None)
    if ring is None or ring == 'openSUSE:Factory:DVD' or ring == 'openSUSE:Factory:MainDesktops':
        msg = "Not core enough for our staging"
    else:
        print "Request(%d): %s -> %s" % (id, tpkg, ring)
        print self.packages_staged.get(tpkg, '')
        return

    self._checkercore_change_review_state(opts, id, 'accepted', by_group='factory-staging', message=msg)

def _checker_parse_staging_prjs(self, opts):
    self.packages_staged = dict()

    for letter in range(ord('A'), ord('J')):
        prj = "openSUSE:Factory:Staging:%s" % chr(letter)
        u = makeurl(opts.apiurl, ['source', prj, '_meta'])
        f = http_GET(u)
        title = ET.parse(f).getroot().find('title').text
        if title is None: continue
        for rq in title.split(','):
            m = re.match(r" *([\w-]+)\((\d+)\)", rq)
            if m is None: continue
            self.packages_staged[m.group(1)] = (chr(letter), m.group(2))

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

    if args[0] == 'skip':
        for id in args[1:]:
           self._checkcore_accept_request(opts, id, "skip review")
        return
    ids = {}
    for a in args:
        if (re.match('\d+', a)):
            ids[a] = 1

    self._checker_parse_staging_prjs(opts)
    self.rings = self._checkercore_get_rings(opts)

    if (not len(ids)):
        # xpath query, using the -m, -r, -s options
        where = "@by_group='factory-staging'+and+@state='new'"

        url = makeurl(opts.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            tprj = rq.find('action/target').get('project')
            self._checkercore_one_request(rq, args[0], opts)
    else:
        # we have a list, use them.
        for id in ids.keys():
            url = makeurl(opts.apiurl, ['request', id])
            f = http_GET(url)
            xml = ET.parse(f)
            root = xml.getroot()
            self._checkercore_one_request(root, args[0], opts)


#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
