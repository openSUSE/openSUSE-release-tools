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

def _check_repo_change_review_state(self, opts, id, newstate, message='', supersed=None):
    """ taken from osc/osc/core.py, improved:
        - verbose option added,
        - empty by_user=& removed.
        - numeric id can be int().
    """
    query = {'cmd': 'changereviewstate', 'newstate': newstate }
    query['by_user']= 'factory-repo-checker'
    if supersed: query['superseded_by'] = supersed
#    if message: query['comment'] = message
    u = makeurl(opts.apiurl, ['request', str(id)], query=query)
    f = http_POST(u, data=message)
    root = ET.parse(f).getroot()
    return root.attrib['code']

def _check_repo_find_submit_request(self, opts, project, package):
    xpath = "(action/target/@project='%s' and action/target/@package='%s' and action/@type='submit' and (state/@name='new' or state/@name='review' or state/@name='accepted'))" % (project, package)
    try:
        url = makeurl(opts.apiurl, ['search','request'], 'match=%s' % quote_plus(xpath))
        f = http_GET(url)
        collection = ET.parse(f).getroot()
    except urllib2.HTTPError:
        print "error"
        return None
    for root in collection.findall('request'):
        r = Request()
        r.read(root)
        return r.reqid
    return None
        
def _check_repo(self, repo):
    allfine = True
    founddisabled = False
    foundbuilding = None
    foundfailed = None
    foundoutdated = None
    missings = {}
    found64 = False
    for arch in repo.findall('arch'):
        if arch.attrib.has_key('missing'):
            for pkg in arch.attrib['missing'].split(','):
                missings[pkg] = 1
        if not (arch.attrib['result'] in ['succeeded', 'excluded']):
            allfine = False
        if arch.attrib['result'] == 'disabled':
            founddisabled = True
        if arch.attrib['result'] == 'failed':
            foundfailed = repo.attrib['name']
        if arch.attrib['result'] == 'building':
            foundbuilding = repo.attrib['name']
        if arch.attrib['result'] == 'outdated':
            foundoutdated = repo.attrib['name']
        if arch.attrib['arch'] == 'x86_64':
            found64 = True

    if not found64:
        allfine = False

    return [allfine, founddisabled, foundbuilding, foundfailed, foundoutdated, missings.keys(), found64]



def _check_repo_one_request(self, rq, opts):

    class CheckRepoPackage:
        def __repr__(self):
            return "[%s/%s]" % (self.sproject, self.spackage)

    if opts.verbose:
        ET.dump(rq)
        print(opts)
    id = int(rq.get('id'))
    approved_actions = 0
    actions = rq.findall('action')
    if len(actions) != 1:
       msg = "only one action per request is supported - create a group instead: https://github.com/SUSE/hackweek/wiki/Improved-Factory-devel-project-submission-workflow"
       print "declined " + msg
       self._check_repo_change_review_state(opts, id, 'declined', message=msg)
       return []
 
    act = actions[0]
    _type = act.get('type')
    if _type != "submit":
        self._check_repo_change_review_state(opts, id, 'accepted',
                                             message="Unchecked request type %s" % _type)
        return []

    pkg = act.find('source').get('package')
    prj = act.find('source').get('project')
    rev = act.find('source').get('rev')
    tprj = act.find('target').get('project')
    tpkg = act.find('target').get('package')

    subm_id = "SUBMIT(%d):" % id
    print "%s %s/%s -> %s/%s" % (subm_id,
                                 prj,  pkg,
                                 tprj, tpkg)

    group = id
    try:
        url = makeurl(opts.apiurl, ["search", "request", "id?match=action/grouped/@id=%s" % id])
        root = ET.parse(http_GET(url)).getroot()
        for req in root.findall('request'):
            group = req.attrib['id']
            break
    except urllib2.HTTPError:
        pass

    packs = []
    p = CheckRepoPackage()
    p.spackage = pkg
    p.sproject = prj
    p.tpackage = tpkg
    p.tproject = tprj
    p.group = group
    p.request = id
    try:
        url = makeurl(opts.apiurl, ["source", prj, pkg, "?expand=1&rev=%s" % rev])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError:
        print "error"
        return []
    #print ET.tostring(root)
    p.rev = root.attrib['srcmd5']
    specs = []
    for entry in root.findall('entry'):
        if not entry.attrib['name'].endswith('.spec'): continue
        name = entry.attrib['name'][:-5]
        specs.append(name)
    # source checker validated it exists
    specs.remove(tpkg)
    packs.append(p)
    for spec in specs:
        lprj = ''
        lpkg = ''
        lmd5 = ''
        try:
            url = makeurl(opts.apiurl, ["source", prj, spec, "?expand=1"])
            root = ET.parse(http_GET(url)).getroot()
            link = root.find('linkinfo')
            lprj = link.attrib['project']
            lpkg = link.attrib['package']
            lmd5 = link.attrib['srcmd5']
        except urllib2.HTTPError:
            pass # leave lprj
        if lprj != prj or lpkg != pkg:
            msg = "%s/%s should _link to %s/%s" % (prj,spec,prj,pkg)
            self._check_repo_change_review_state(opts, id, 'declined', message=msg)
            return []
        if lmd5 != p.rev:
            msg = "%s/%s is a link but has a different md5sum than %s?" % (prj,spec,pkg)
            self._check_repo_change_review_state(opts, id, 'new', message=msg)
            return []

        sp = CheckRepoPackage()
        sp.spackage = spec
        sp.sproject = prj
        sp.tpackage = spec
        sp.tproject = tprj
        sp.group = p.group
        sp.request = id
        packs.append(sp)
        sp.rev = root.attrib['srcmd5']
    return packs

def _check_repo_buildsuccess(self, p, opts):
    try:
        url = makeurl(opts.apiurl, ['build', p.sproject, "_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s" % (p.spackage,p.tproject,p.rev)])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError:
        print "error"
        return False
    if root.attrib.has_key('code'):
        print ET.tostring(root)
        return False
    result = False
    p.goodrepo = None
    missings = {}
    alldisabled = True
    foundbuilding = None
    foundfailed = None
    foundoutdated = None
    found64 = True
    for repo in root.findall('repository'):
        [isgood, founddisabled, r_foundbuilding, r_foundfailed, r_foundoutdated, r_missings, r_found64] = self._check_repo(repo)
        for rm in r_missings:
            missings[rm] = 1
        if not founddisabled:
            alldisabled = False
        if isgood:
            if len(missings) == 0 or os.environ.has_key("IGNORE_MISSINGS"):
                p.goodrepo = repo.attrib['name']
                result = True
                missings = {}
        if r_foundbuilding:
             foundbuilding = r_foundbuilding
        if r_foundfailed:
             foundfailed = r_foundfailed
        if r_foundoutdated:
             foundoutdated = r_foundoutdated
        if r_found64:
            found64 = r_found64

    if result: 
        return True

    if foundoutdated:
        msg = "the package sources were changed after submissions and the old sources never built. Please resubmit"
        print "declined " + msg
        self._check_repo_change_review_state(opts, p.request, 'declined', message=msg)
        return False
    if alldisabled:
        msg = "the package is disabled or does not build against factory. Please fix and resubmit"
        print "declined " + msg
        self._check_repo_change_review_state(opts, p.request, 'declined', message=msg)
        return False
    if len(missings.keys()):
        smissing = []
        missings.keys().sort()
        print missings
        for package in missings:
            request = self._check_repo_find_submit_request(opts, p.tproject, package)
            if request:
               package = "%s(rq%s)" % (package, request) 
            smissing.append(package)

        msg = "please make sure to wait before these depencencies are in {0}: {1}".format(p.tproject, ', '.join(smissing))
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False
    if foundbuilding:	
        msg = "the package is still building for repository {0}".format(foundbuilding)
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False
    if foundfailed:
        msg = "the package failed to build in repository {0} - not accepting".format(foundfailed)
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False
    if not found64:
        msg = "Missing x86_64 in the repo list"
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False

    return True

def _check_repo_download(self, p, opts):
    
    p.groupdir = os.path.expanduser("~/co/%s" % str(p.group))
    p.destdir = opts.destdir = p.groupdir + "/%s" % p.tpackage
    if not os.path.isdir(p.destdir):
      os.makedirs(p.destdir, 0755)
    opts.sources = False
    opts.debug = False
    opts.quiet = True
    # we can assume x86_64 is there
    self.do_getbinaries(None, opts, p.sproject, p.spackage, p.goodrepo, 'x86_64')

    # now fetch -32bit packs
    url = makeurl(opts.apiurl, ['build', p.sproject, p.goodrepo, 'i586', p.spackage])
    try:
      f = http_GET(url)
      binaries = ET.parse(f).getroot()
      for bin in  binaries.findall('binary'):
        fn=bin.attrib['filename']
        result = re.match("(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm", fn)
        if not result: continue
        if result.group(4) != 'x86_64': continue
        get_binary_file(opts.apiurl,
                        p.sproject, p.goodrepo, 'i586', fn, 
                        package = p.spackage, target_filename = os.path.join(opts.destdir, fn))
    except urllib2.HTTPError, err:
      print err
      pass

    url = makeurl(opts.apiurl, ['build', p.tproject, 'standard', 'x86_64', p.tpackage])
    toignore = []
    try:
      f = http_GET(url)
      binaries = ET.parse(f).getroot()
      for bin in  binaries.findall('binary'):
        fn=bin.attrib['filename']
        result = re.match("(.*)-([^-]*)-([^-]*)\.[^-\.]*.rpm", fn) 
        if not result: continue
        toignore.append(result.group(1))
    except urllib2.HTTPError, err:
       pass
    # now fetch -32bit pack list
    url = makeurl(opts.apiurl, ['build', p.tproject, 'standard', 'i586', p.tpackage])
    try:
      f = http_GET(url)
      binaries = ET.parse(f).getroot()
      for bin in  binaries.findall('binary'):
        fn=bin.attrib['filename']
        result = re.match("(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm", fn)
        if not result: continue
        if result.group(4) != 'x86_64': continue
        toignore.append(result.group(1))
    except urllib2.HTTPError, err:
       pass
    return toignore

def _check_repo_group(self, id, reqs, opts):
    print "check group", reqs
    for p in reqs:
        if not self._check_repo_buildsuccess(p, opts):
            return
    # all succeeded
    toignore = []
    destdir = ''
    for p in reqs:
        toignore.extend(self._check_repo_download(p, opts))
        destdir = p.groupdir

    civs = "LC_ALL=C perl /suse/coolo/checker/repo-checker.pl '%s' '%s' 2>&1" % (destdir, ','.join(toignore))
    p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, close_fds=True)
    ret = os.waitpid(p.pid, 0)[1]
    checked = p.stdout.readlines()
    output = '  '.join(checked).translate(None, '\033')
    shutil.rmtree(destdir)
    
    updated = dict()

    if ret:
        print output, set(map(lambda x: x.request, reqs))

        for p in reqs:
            if updated.get(p.request, False): continue
            self._check_repo_change_review_state(opts, p.request, 'new', message=output)
            updated[p.request] = 1
        return
    for p in reqs:
        if updated.get(p.request, False): continue
        msg="Builds for repo %s" % p.goodrepo
        self._check_repo_change_review_state(opts, p.request, 'accepted', message=msg)
        updated[p.request] = 1

def do_check_repo(self, subcmd, opts, *args):
    """${cmd_name}: checker review of submit requests.

    Usage:
      osc checker [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    if len(args) == 0:
        raise oscerr.WrongArgs("Please give a subcommand to 'osc checker' or try 'osc help checker'")

    opts.mode = ''
    opts.groups = {}
    opts.verbose = False

    from pprint import pprint

    opts.apiurl = self.get_api_url()

    tmphome = None

    if args[0] == 'skip':
        for id in args[1:]:
            self._check_repo_change_review_state(opts, id, 'accepted', message="skip review")
        return
    ids = {}
    for a in args:
        if (re.match('\d+', a)):
            ids[a] = 1

    packs = []
    if (not len(ids)):
        # xpath query, using the -m, -r, -s options
        where = "@by_user='factory-repo-checker'+and+@state='new'"

        url = makeurl(opts.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            tprj = rq.find('action/target').get('project')
            packs.extend(self._check_repo_one_request(rq, opts))
    else:
        # we have a list, use them.
        for id in ids.keys():
            url = makeurl(opts.apiurl, ['request', id])
            f = http_GET(url)
            xml = ET.parse(f)
            root = xml.getroot()
            packs.extend(self._check_repo_one_request(root, opts))

    groups = {}
    for p in packs:
        a = groups.get(p.group, [])
        a.append(p)
        groups[p.group] = a

    for id, reqs in groups.items():
        self._check_repo_group(id, reqs, opts)

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
