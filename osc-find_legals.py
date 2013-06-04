#
# (C) 2013 coolo@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc checker --help' to see the usage.

def _find_legals(self, package, opts):
    factory_time, factory_who, version_updates = self._find_legal_reviews("openSUSE:Factory", package, opts)
    queue_time, queue_who, dummy = self._find_legal_reviews("devel:openSUSE:Factory:legal-queue", package, opts)
    if factory_time > queue_time:
       queue_time = factory_time
       queue_who = factory_who
    #print("F", package, queue_who, queue_time, version_updates)
    return queue_time, version_updates

def _find_legal_get_versions_update(self, review):
    text = review.find('comment').text
    text = re.sub(r'.*<!--(.*)', r'\1', text)
    text = re.sub(r'-->.*', '', text)
    import json
    try:
    	text = json.loads(text)
    except ValueError:
	return False
    try:
       dver = text.get('dest', {}).get('version', None)
       sver = text.get('src', {}).get('version', None)
    except AttributeError:
	return False
    if dver and sver and dver != sver:
   	return True
    return False

def _find_legal_reviews(self, project, package, opts):
    lastreview=time.gmtime(0)
    lastupdate=None
    lastwho='noone'
    url = makeurl(opts.apiurl, ['request'], "states=new,superseded,review,accepted,declined,revoked&project=%s&view=collection&package=%s" % (project, package) )
    f = http_GET(url)
    root = ET.parse(f).getroot()
    rqs = {}
    for rq in root.findall('request'):
        #print(ET.dump(rq))
	id = rq.attrib['id']
        for review in rq.findall('review'):
          if not review.attrib.get('when'): continue
          when = time.strptime(review.attrib['when'], '%Y-%m-%dT%H:%M:%S')

	  if review.attrib.get('by_group') == 'legal-auto': 
		if self._find_legal_get_versions_update(review) and not lastupdate:
			lastupdate=when
		continue
	  if review.attrib.get('by_group') != 'legal-team': continue
	  who = review.attrib.get('who')
          if who == 'factory-maintainer': continue
          if when > lastreview:
		lastreview = when
		lastwho=who
		lastupdate=None
    return lastreview, lastwho, lastupdate

def do_find_legals(self, subcmd, opts, *args):
    """${cmd_name}: checker review of submit requests.

    Usage:
      osc check_dups [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    opts.apiurl = self.get_api_url()

    def _find_legal_cmp(t1, t2):
	if t1[1] < t2[1]:
		return -1
	if t1[1] > t2[1]:
		return 1
	if t1 and t2 and t1[2] < t2[2]:
		return 1
	if t1 and not t2:
		return -1
	if t2 and not t1:
		return 1
	return -1

    packages = list()
    for p in args[:]:
       lastreview, lastupdate = self._find_legals(p, opts)
       packages.append((p, lastreview, lastupdate))
    packages = sorted(packages, cmp=_find_legal_cmp)
    print("ORDER")
    for p in packages:
	update = 'never'
	if p[2]: 
		update = time.asctime(p[2])
	print(p[0], time.asctime(p[1]), update)

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
