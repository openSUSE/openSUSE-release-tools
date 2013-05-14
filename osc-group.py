#
# (C) 2013 coolo@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc checker --help' to see the usage.

def _group_find_request(self, package, opts):
    url = makeurl(opts.apiurl, ['request'], "states=new,review,declined&project=openSUSE:Factory&view=collection&package=%s" % package )
    f = http_GET(url)
    root = ET.parse(f).getroot()
    maxid=0
    for rq in root.findall('request'):
        #print(ET.dump(rq))
	id = int(rq.attrib['id'])
        if id > maxid:
            maxid = id
    return maxid

def _group_find_group(self, request, opts):
    url = makeurl(opts.apiurl, ['search', "request", "id?match=action/grouped/@id=%s" % request] )
    f = http_GET(url)
    root = ET.parse(f).getroot()
    maxid=0
    for rq in root.findall('request'):
        #print(ET.dump(rq))
	id = int(rq.attrib['id'])
        if id > maxid:
            maxid = id
    return maxid


def do_group(self, subcmd, opts, *args):
    """${cmd_name}: group packages

    Usage:
      osc group [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    opts.apiurl = self.get_api_url()

    requests=[]
    grouptoadd=0
    for p in args[:]:
        request = self._group_find_request(p, opts)
        if not request:
            print("Can't find a request for", p)
            exit(1)
        group = self._group_find_group(request, opts)
        if group > 0:
            if grouptoadd > 0 and grouptoadd != group:
                print("there are two groups:", grouptoadd, group)
                exit(1)
            else:
                grouptoadd = group
        else:
            requests.append(request)

    if grouptoadd > 0:
        for r in requests:
            query = {'cmd': 'addrequest'}
            query['newid'] = str(r)
            u = makeurl(opts.apiurl, ['request', str(grouptoadd)], query=query)
            f = http_POST(u)
            root = ET.parse(f).getroot()
            print("added", r, "to group", grouptoadd)
    else:
        xml='<request><action type="group">'
        for r in requests:
            xml += "<grouped id='" + str(r) + "'/>"
        xml+='</action><description>'
        xml+= ' '.join(args[:])
        xml+='</description></request>'
        query = {'cmd': 'create' }
        u = makeurl(opts.apiurl, ['request'], query=query)
        f = http_POST(u, data=xml)
        root = ET.parse(f).getroot()
        ET.dump(root)

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
