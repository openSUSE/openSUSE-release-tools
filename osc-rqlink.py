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

def do_rqlink(self, subcmd, opts, *args):
    """${cmd_name}: link request sources

    Usage:
      osc rqlink [OPT] [ID] [PRJ]
           link the request's sources into prj

    ${cmd_option_list}
    """

    if len(args) != 2:
        raise oscerr.WrongArgs("Please give an id and a prj")

    opts.verbose = False

    from pprint import pprint

    opts.apiurl = self.get_api_url()

    rqid = args[0]
    url = makeurl(opts.apiurl, ['request', rqid])
    f = http_GET(url)
    rq =  ET.parse(f).getroot()
    act = rq.findall('action')[0]

    _type = act.get('type');
    if (_type != "submit"):
        raise "Can't handle %s" % _type
        
    pkg = act.find('source').get('package')
    prj = act.find('source').get('project')
    rev = act.find('source').get('rev')
    tpkg = act.find('target').get('package')

    url =  makeurl(opts.apiurl, ['source', prj, pkg], { 'rev': rev, 'expand': 1 })
    f = http_GET(url)
    rev =  ET.parse(f).getroot().attrib['srcmd5']
    print "osc linkpac -r %s %s/%s %s/%s" % (rev, prj, pkg, args[1], tpkg)
    link_pac(prj, pkg, args[1], tpkg, force=True, rev=rev)

    url =  makeurl(opts.apiurl, ['source', args[1], '_meta'])
    f = http_GET(url)
    root = ET.parse(f).getroot()
    title = root.find('title')
    text = title.text
    rqtext = "%s(%s)" % (pkg, rqid)
    if text is None:
       text = rqtext
    else:
       text = text + (", %s" % rqtext)
    title.text = text
    http_PUT(url, data=ET.tostring(root))
    #print pkg, prj, rev

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
