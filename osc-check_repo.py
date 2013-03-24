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
	xpath = "(action/target/@project='%s' and action/target/@package='%s')" % (project, package)
        url = makeurl(opts.apiurl, ['search','request'], 'match=%s' % quote_plus(xpath))
        f = http_GET(url)
        collection = ET.parse(f).getroot()
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

def _check_repo_one_request(self, rq, cmd, opts):
    if (opts.verbose):
        ET.dump(rq)
        print(opts)
    id = int(rq.get('id'))
    act_id = 0
    approved_actions = 0
    actions = rq.findall('action')
    if len(actions) > 1:
       msg = "2 actions in one SR is not supported - https://github.com/coolo/factory-auto/fork_select"
       print "declined " + msg
       self._check_repo_change_review_state(opts, id, 'declined', message=msg)
       return
 
    for act in actions:
        act_id += 1
        _type = act.get('type');
        if (_type == "submit"):
            pkg = act.find('source').get('package')
            prj = act.find('source').get('project')
            rev = act.find('source').get('rev')
            tprj = act.find('target').get('project')
            tpkg = act.find('target').get('package')

            src = { 'package': pkg, 'project': prj, 'rev':rev, 'error': None }
            e = []
            if not pkg:
                e.append('no source/package in request %d, action %d' % (id, act_id))
            if not prj:
                e.append('no source/project in request %d, action %d' % (id, act_id))
            if len(e): src.error = '; '.join(e)

            e = []
            if not tpkg:
                e.append('no target/package in request %d, action %d; ' % (id, act_id))
            if not prj:
                e.append('no target/project in request %d, action %d; ' % (id, act_id))
            # it is no error, if the target package dies not exist

            subm_id = "SUBMIT(%d):" % id
            print "\n%s %s/%s -> %s/%s" % (subm_id,
                                           prj,  pkg,
                                           tprj, tpkg)
            try:
                url = makeurl(opts.apiurl, ['status', "bsrequest?id=%d" % id])
                root = ET.parse(http_GET(url)).getroot()
            except urllib2.HTTPError:
                print "error"
                continue
            if root.attrib.has_key('code'):
                print ET.tostring(root)
                continue
            result = False
            goodrepo = None
            missings = {}
            alldisabled = True
	    foundbuilding = None
	    foundfailed = None
	    foundoutdated = None
            found64 = True
            for repo in root.findall('repository'):
                [isgood, founddisabled, r_foundbuilding, r_foundfailed, r_foundoutdated, r_missings, r_found64] = self._check_repo(repo)
		for p in r_missings:
		    missings[p] = 1
                if not founddisabled:
                    alldisabled = False
                if isgood:
                    if len(missings) == 0 or os.environ.has_key("IGNORE_MISSINGS"):
                        goodrepo = repo
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

            if result == False:
                if foundoutdated:
                    msg = "the package sources were changed after submissions and the old sources never built. Please resubmit"
                    print "declined " + msg
                    self._check_repo_change_review_state(opts, id, 'declined', message=msg)
                    continue
                if alldisabled:
                    msg = "the package is disabled or does not build against factory. Please fix and resubmit"
                    print "declined " + msg
                    self._check_repo_change_review_state(opts, id, 'declined', message=msg)
                    continue
                if len(missings.keys()):
	            smissing = []
		    missings.keys().sort()
                    for package in missings:
			request = self._check_repo_find_submit_request(opts, tprj, package)
			if request:
			   package = "%s(rq%s)" % (package, request) 
                        smissing.append(package)

                    msg = "please make sure to wait before these depencencies are in {0}: {1}".format(tprj, ', '.join(smissing))
                    self._check_repo_change_review_state(opts, id, 'new', message=msg)
                    print "updated " + msg
                    continue
		if foundbuilding:	
		    msg = "the package is still building for repository {0}".format(foundbuilding)
		    self._check_repo_change_review_state(opts, id, 'new', message=msg)
                    print "updated " + msg
		    continue
	        if foundfailed:
                    msg = "the package failed to build in repository {0} - not accepting".format(foundfailed)
                    self._check_repo_change_review_state(opts, id, 'new', message=msg)
		    print "updated " + msg
                    continue
                if not found64:
                    msg = "Missing x86_64 in the repo list"
                    self._check_repo_change_review_state(opts, id, 'new', message=msg)
		    print "updated " + msg
                    continue

                print ET.tostring(root)
                continue

            opts.destdir = os.path.expanduser("~/co/%s" % str(id))
            opts.sources = False
            opts.debug = False
            opts.quiet = True
            # we can assume x86_64 is there
            self.do_getbinaries(None, opts, prj, pkg, goodrepo.attrib['name'], 'x86_64')

            # now fetch -32bit packs
            url = makeurl(opts.apiurl, ['build', prj, goodrepo.attrib['name'], 'i586', pkg])
            try:
              f = http_GET(url)
              binaries = ET.parse(f).getroot()
              for bin in  binaries.findall('binary'):
                fn=bin.attrib['filename']
                result = re.match("(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm", fn)
                if not result: continue
                if result.group(4) != 'x86_64': continue
                get_binary_file(opts.apiurl,
                                prj, goodrepo.attrib['name'], 'i586', fn, package = pkg, target_filename = os.path.join(opts.destdir, fn))
            except urllib2.HTTPError, err:
              print err
              pass

            url = makeurl(opts.apiurl, ['build',tprj, 'standard', 'x86_64', tpkg])
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
               print "new package?"
            # now fetch -32bit pack list
            url = makeurl(opts.apiurl, ['build',tprj, 'standard', 'i586', tpkg])
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
               print "new package?"


            civs = "LC_ALL=C perl /suse/coolo/checker/repo-checker.pl '%s' '%s' 2>&1" % (opts.destdir, ','.join(toignore))
            p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, close_fds=True)
            ret = os.waitpid(p.pid, 0)[1]
            checked = p.stdout.readlines()
            output = '  '.join(checked).translate(None, '\033')
            shutil.rmtree(opts.destdir)

	    if ret:
                print ret, "OUT", output
                continue

            msg="Builds for repo %s" % goodrepo.attrib['name']
                
            self._check_repo_change_review_state(opts, id, 'accepted', message=msg)

            if cmd == "list":
                pass
            else:
                print "unknown command: %s" % cmd
        else:
            self._check_repo_change_review_state(opts, id, 'accepted',
                                              message="Unchecked request type %s" % _type)

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

    if (not len(ids)):
        # xpath query, using the -m, -r, -s options
        where = "@by_user='factory-repo-checker'+and+@state='new'"

        url = makeurl(opts.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            tprj = rq.find('action/target').get('project')
            self._check_repo_one_request(rq, args[0], opts)
    else:
        # we have a list, use them.
        for id in ids.keys():
            url = makeurl(opts.apiurl, ['request', id])
            f = http_GET(url)
            xml = ET.parse(f)
            root = xml.getroot()
            self._check_repo_one_request(root, args[0], opts)

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
