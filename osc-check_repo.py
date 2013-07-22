#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc check_repo --help' to see the usage.

import os
import re
import subprocess
import shutil
from urllib import quote_plus
import urllib2

from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import (get_binary_file,
                      get_buildinfo,
                      http_GET,
                      http_POST,
                      makeurl,
                      Request)


def _check_repo_change_review_state(self, opts, id_, newstate, message='', supersed=None):
    """ taken from osc/osc/core.py, improved:
        - verbose option added,
        - empty by_user=& removed.
        - numeric id can be int().
    """
    query = {
        'cmd': 'changereviewstate',
        'newstate': newstate,
        'by_user': 'factory-repo-checker',
    }
    if supersed:
        query['superseded_by'] = supersed
    # if message:
    #     query['comment'] = message

    code = 404
    u = makeurl(opts.apiurl, ['request', str(id_)], query=query)
    try:
        f = http_POST(u, data=message)
        root = ET.parse(f).getroot()
        code = root.attrib['code']
    except urllib2.HTTPError, e:
        print 'ERROR in URL %s [%s]'%(u, e)
    return code


def _check_repo_find_submit_request(self, opts, project, package):
    xpath = "(action/target/@project='%s' and action/target/@package='%s' and action/@type='submit' and (state/@name='new' or state/@name='review' or state/@name='accepted'))" % (project, package)
    try:
        url = makeurl(opts.apiurl, ['search','request'], 'match=%s' % quote_plus(xpath))
        f = http_GET(url)
        collection = ET.parse(f).getroot()
    except urllib2.HTTPError:
        print "error", url
        return None
    for root in collection.findall('request'):
        r = Request()
        r.read(root)
        return int(r.reqid)
    return None

        
def _check_repo_fetch_group(self, opts, group):
    if opts.groups.get(group): return
    u = makeurl(opts.apiurl, ['request', str(group)])
    f = http_GET(u)
    root = ET.parse(f).getroot()
    a = []
    for req in root.find('action').findall('grouped'):
        id_ = int(req.attrib['id'])
        a.append(id)
        opts.grouped[id_] = group
    opts.groups[group] = a


def _check_repo_avoid_wrong_friends(self, prj, repo, arch, pkg, opts):
    try:
        url = makeurl(opts.apiurl, ["build", prj, repo, arch, pkg])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError:
        print "error", url
        return False
    for binary in root.findall('binary'):
        # if there are binaries, we're out
        return False
    return True


def _check_repo_one_request(self, rq, opts):

    class CheckRepoPackage:
        def __repr__(self):
            return '[%d:%s/%s]' % (int(self.request), self.sproject, self.spackage)

	def __init__(self):
	    self.updated = False
            self.error = None
            self.build_excluded = False


    id_ = int(rq.get('id'))
    actions = rq.findall('action')
    if len(actions) > 1:
       msg = 'only one action per request is supported - create a group instead: '\
             'https://github.com/SUSE/hackweek/wiki/Improved-Factory-devel-project-submission-workflow'
       print 'declined ' + msg
       self._check_repo_change_review_state(opts, id_, 'declined', message=msg)
       return []
 
    act = actions[0]
    type_ = act.get('type')
    if type_ != 'submit':
        self._check_repo_change_review_state(opts, id_, 'accepted',
                                             message='Unchecked request type %s'%type_)
        return []

    pkg = act.find('source').get('package')
    prj = act.find('source').get('project')
    rev = act.find('source').get('rev')
    tprj = act.find('target').get('project')
    tpkg = act.find('target').get('package')

    subm_id = 'SUBMIT(%d):' % id_
    print '%s %s/%s -> %s/%s' % (subm_id,
                                 prj,  pkg,
                                 tprj, tpkg)

    group = id_
    try:
        if opts.grouped.has_key(id_):
            group = opts.grouped[id_]
        else:
            url = makeurl(opts.apiurl, ["search", "request", "id?match=action/grouped/@id=%s" % id_])
            root = ET.parse(http_GET(url)).getroot()
            for req in root.findall('request'):
                group = int(req.attrib['id'])
                self._check_repo_fetch_group(opts, group)
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
    p.request = id_
    try:
        url = makeurl(opts.apiurl, ["source", prj, pkg, "?expand=1&rev=%s" % rev])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError:
        print "error", url
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
            if link != None:
                lprj = link.attrib.get('project', '')
                lpkg = link.attrib.get('package', '')
                lmd5 = link.attrib['srcmd5']
        except urllib2.HTTPError:
            pass # leave lprj
        if lprj != prj or lpkg != pkg and not p.updated:
            msg = "%s/%s should _link to %s/%s" % (prj,spec,prj,pkg)
            self._check_repo_change_review_state(opts, id_, 'declined', message=msg)
            print msg
            p.updated = True
        if lmd5 != p.rev and not p.updated:
            msg = "%s/%s is a link but has a different md5sum than %s?" % (prj,spec,pkg)
            self._check_repo_change_review_state(opts, id_, 'new', message=msg)
            print msg
            p.updated = True

        sp = CheckRepoPackage()
        sp.spackage = spec
        sp.sproject = prj
        sp.tpackage = spec
        sp.tproject = tprj
        sp.group = p.group
        sp.request = id_
        packs.append(sp)
        sp.rev = root.attrib['srcmd5']
    return packs


def _check_repo_buildsuccess(self, p, opts):
    try:
        url = makeurl(opts.apiurl, ['build', p.sproject, "_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s" % (quote_plus(p.spackage), quote_plus(p.tproject), p.rev)])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError:
        print "error", url
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

    tocheckrepos = []
    for repo in root.findall('repository'):
        foundarchs=0
        for arch in repo.findall('arch'):
            arch = arch.attrib['arch']
            if arch == 'i586':
                foundarchs += 1
            if arch == 'x86_64':
                foundarchs += 1
        if foundarchs == 2:
            tocheckrepos.append(repo)
            
    if len(tocheckrepos) == 0:
        msg = "Missing i586 and x86_64 in the repo list"
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False
        
    for repo in tocheckrepos:
        isgood = True
        founddisabled = False
        r_foundbuilding = None
        r_foundfailed = None
        r_missings = {}
        for arch in repo.findall('arch'):
            if not (arch.attrib['arch'] == 'i586' or arch.attrib['arch'] == 'x86_64'):
                continue
            if arch.attrib.has_key('missing'):
                for pkg in arch.attrib['missing'].split(','):
                    if not self._check_repo_avoid_wrong_friends(p.sproject, repo.attrib['name'], arch.attrib['arch'], pkg, opts):
                        missings[pkg] = 1
            if not (arch.attrib['result'] in ['succeeded', 'excluded']):
                isgood = False
            if arch.attrib['result'] == 'excluded' and arch.attrib['arch'] == 'x86_64':
                p.build_excluded = True
            if arch.attrib['result'] == 'disabled':
                founddisabled = True
            if arch.attrib['result'] == 'failed':
                r_foundfailed = repo.attrib['name']
            if arch.attrib['result'] == 'building':
                r_foundbuilding = repo.attrib['name']
            if arch.attrib['result'] == 'outdated':
                msg = "%s's sources were changed after submissions and the old sources never built. Please resubmit" % p.spackage
                print "declined " + msg
                self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
                return False

        r_missings = r_missings.keys()
        for pkg in r_missings:
            missings[pkg] = 1
        if not founddisabled:
            alldisabled = False
        if isgood:
            p.goodrepo = repo.attrib['name']
            result = True
        if r_foundbuilding:
             foundbuilding = r_foundbuilding
        if r_foundfailed:
             foundfailed = r_foundfailed

    p.missings = missings.keys()
    p.missings.sort()

    if result:
        return True

    if alldisabled:
        msg = "%s is disabled or does not build against factory. Please fix and resubmit" % p.spackage
        print "declined " + msg
        self._check_repo_change_review_state(opts, p.request, 'declined', message=msg)
        return False
    if foundbuilding:	
        msg = "{1} is still building for repository {0}".format(foundbuilding, p.spackage)
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False
    if foundfailed:
        msg = "{1} failed to build in repository {0} - not accepting".format(foundfailed, p.spackage)
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        print "updated " + msg
        return False

    return True


def _check_repo_repo_list(self, prj, repo, arch, pkg, opts):
    url = makeurl(opts.apiurl, ['build', prj, repo, arch, pkg])
    files = []
    try:
        f = http_GET(url)
        binaries = ET.parse(f).getroot()
        for bin in  binaries.findall('binary'):
            fn=bin.attrib['filename']
            result = re.match("(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm", fn)
            if not result: 
                if fn == 'rpmlint.log':
                    files.append((fn, '', ''))
                continue
            pname=result.group(1)
            if pname.endswith('-debuginfo') or pname.endswith('-debuginfo-32bit'):
                continue
            if pname.endswith('-debugsource'):
                continue
            if result.group(4) == 'src':
                continue
            files.append((fn, pname, result.group(4)))
    except urllib2.HTTPError:
        print "error", url
    return files


def _check_repo_get_binary(self, apiurl, prj, repo, arch, package, file, target):
    if os.path.exists(target):
        return
    get_binary_file(apiurl, prj, repo, arch, file, package = package, target_filename = target)


def _check_repo_download(self, p, destdir, opts):
    if p.build_excluded:
        return [], []

    p.destdir = destdir + "/%s" % p.tpackage
    if not os.path.isdir(p.destdir):
      os.makedirs(p.destdir, 0755)
    # we can assume x86_64 is there
    todownload = []
    for fn in self._check_repo_repo_list(p.sproject, p.goodrepo, 'x86_64', p.spackage, opts):
        todownload.append(('x86_64', fn[0]))
        
    # now fetch -32bit packs
    for fn in self._check_repo_repo_list(p.sproject, p.goodrepo, 'i586', p.spackage, opts):
        if fn[2] != 'x86_64': continue
        todownload.append(('i586', fn[0]))
        
    downloads = []
    for arch, fn in todownload:
        t = os.path.join(p.destdir, fn)
        self._check_repo_get_binary(opts.apiurl, p.sproject, p.goodrepo, 
                                    arch, p.spackage, fn, t)
        downloads.append(t)
        if fn.endswith('.rpm'):
            pid = subprocess.Popen(["rpm", "--nosignature", "--queryformat", "%{DISTURL}", "-qp", t], 
                                   stdout=subprocess.PIPE, close_fds=True)
            ret = os.waitpid(pid.pid, 0)[1]
            disturl = pid.stdout.readlines()
            
            if not os.path.basename(disturl[0]).startswith(p.rev):
                p.error = "disturl %s does not match revision %s" % (disturl[0], p.rev)
                return [], []

    toignore = []
    for fn in self._check_repo_repo_list(p.tproject, 'standard', 'x86_64', p.tpackage, opts):
        toignore.append(fn[1])

    # now fetch -32bit pack list
    for fn in self._check_repo_repo_list(p.tproject, 'standard', 'i586', p.tpackage, opts):
        if fn[2] != 'x86_64': continue
        toignore.append(fn[1])
    return toignore, downloads


def _get_build_deps(self, prj, repo, arch, pkg, opts):
    xml = get_buildinfo(opts.apiurl, prj, pkg, repo, arch)
    root = ET.fromstring(xml)
    return [e.attrib['name'] for e in root.findall('bdep')]


def _get_base_build_bin(self, opts):
    """Get Base:build pagacke list"""
    binaries = {}
    for arch in ('x86_64', 'i586'):
        url = makeurl(opts.apiurl, ['/build/Base:build/standard/%s/_repository'%arch,])
        f = http_GET(url)
        root = ET.parse(f).getroot()
        binaries[arch] = set([e.attrib['filename'][:-4] for e in root.findall('binary')])
    return binaries


def _get_base_build_src(self, opts):
    """Get Base:build pagacke list"""
    url = makeurl(opts.apiurl, ['/source/Base:build',])
    f = http_GET(url)
    root = ET.parse(f).getroot()
    return set([e.attrib['name'] for e in root.findall('entry')])


def _check_repo_group(self, id_, reqs, opts):
    print "\ncheck group", reqs
    for p in reqs:
        if not self._check_repo_buildsuccess(p, opts):
            return
    # all succeeded
    toignore = []
    downloads = []
    destdir = os.path.expanduser("~/co/%s" % str(p.group))
    fetched = dict()
    for r in opts.groups.get(id_, []):
        fetched[r] = False
    goodrepo = ''
    packs = []
    for p in reqs:
        i, d = self._check_repo_download(p, destdir, opts)
        if p.error:
            print p.error
            p.updated = True
            self._check_repo_change_review_state(opts, p.request, 'new', message=p.error)
            return
        downloads.extend(d)
        toignore.extend(i)
        fetched[p.request] = True
        goodrepo = p.goodrepo
        packs.append(p)

    for req, f in fetched.items():
        if not f: 
            packs.extend(self._check_repo_fetch_request(req, opts))
    for p in packs:
        p.goodrepo = goodrepo
        i, d = self._check_repo_download(p, destdir, opts)
        if p.error:
            print "already accepted: ", p.error
            p.updated = True
        downloads.extend(d)
        toignore.extend(i)

    # Get all the Base:build packages (source and binary)
    base_build_bin = self._get_base_build_bin(opts)
    base_build_src = self._get_base_build_src(opts)
    for p in reqs:
        # Be sure that if the package is in Base:build, all the
        # dependecies are also in Base:build
        if p.spackage in base_build_src:
            # TODO - Check all the arch for this package
            for arch in ('x86_64', 'i586'):
                build_deps = set(self._get_build_deps(p.sproject, p.goodrepo, arch, p.spackage, opts))
                outliers = build_deps - base_build_bin[arch]
                if outliers:
                    print 'Outliers (%s)'%arch, outliers
                    

    for p in reqs:
        smissing = []
        for package in p.missings:
            alreadyin=False
            print package, packs
            for t in packs:
                if package == t.tpackage: alreadyin=True
            if alreadyin:
                continue
            print package, packs, downloads, toignore
            request = self._check_repo_find_submit_request(opts, p.tproject, package)
            if request:
                greqs = opts.groups.get(p.group, [])
                if request in greqs: continue
                package = "%s(rq%s)" % (package, request) 
            smissing.append(package)
        if len(smissing):
            msg = "please make sure to wait before these depencencies are in {0}: {1}".format(p.tproject, ', '.join(smissing))
            self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
            print "updated " + msg
            return

    for dirname, dirnames, filenames in os.walk(destdir):
        if len(dirnames) + len(filenames) == 0:
            os.rmdir(dirname)
        for filename in filenames:
            fn = os.path.join(dirname, filename)
            if not fn in downloads:
                os.unlink(fn)

    civs = "LC_ALL=C perl /suse/coolo/checker/repo-checker.pl '%s' '%s' 2>&1" % (destdir, ','.join(toignore))
    #exit(1)
    p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, close_fds=True)
    #ret = os.waitpid(p.pid, 0)[1]
    output, _ = p.communicate()
    ret = p.returncode
    
    updated = dict()

    if ret:
        print output, set(map(lambda x: x.request, reqs))

        for p in reqs:
            if updated.get(p.request, False) or p.updated: continue
            self._check_repo_change_review_state(opts, p.request, 'new', message=output)
            updated[p.request] = 1
	    p.updated = True
        return
    for p in reqs:
        if updated.get(p.request, False) or p.updated: continue
        msg="Builds for repo %s" % p.goodrepo
        self._check_repo_change_review_state(opts, p.request, 'accepted', message=msg)
        updated[p.request] = 1
	p.updated = True
    shutil.rmtree(destdir)


def _check_repo_fetch_request(self, id_, opts):
    url = makeurl(opts.apiurl, ['request', str(id_)])
    f = http_GET(url)
    xml = ET.parse(f)
    root = xml.getroot()
    return self._check_repo_one_request(root, opts)


def do_check_repo(self, subcmd, opts, *args):
    """${cmd_name}: checker review of submit requests.

    Usage:
      osc check_repo [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    if not len(args):
        raise oscerr.WrongArgs("Please give a subcommand to 'osc check_repo' or try 'osc help check_repo'")

    opts.mode = ''
    opts.groups = {}
    opts.grouped = {}
    opts.verbose = False

    opts.apiurl = self.get_api_url()

    if args[0] == 'skip':
        for id_ in args[1:]:
            self._check_repo_change_review_state(opts, id_, 'accepted', message='skip review')
        return

    ids = [arg for arg in args if arg.isdigit()]

    packs = []
    if not ids:
        # xpath query, using the -m, -r, -s options
        where = "@by_user='factory-repo-checker'+and+@state='new'"
        url = makeurl(opts.apiurl, ['search', 'request'], 
                      "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            packs.extend(self._check_repo_one_request(rq, opts))
    else:
        # we have a list, use them.
        for id_ in ids:
	    packs.extend(self._check_repo_fetch_request(id_, opts))

    groups = {}
    for p in packs:
        a = groups.get(p.group, [])
        a.append(p)
        groups[p.group] = a

    # for id_, reqs in groups.items():
    #    self._check_repo_group(id_, reqs, opts)
