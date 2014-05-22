#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc check_repo --help' to see the usage.

import cPickle
from copy import deepcopy
from datetime import datetime
from functools import wraps
import fcntl
import os
import re
import shelve
import shutil
import subprocess
import tempfile
import sys

from urllib import quote_plus
import urllib2
from xml.etree import cElementTree as ET

from osc import oscerr
from osc import cmdln

from osc.core import get_binary_file
from osc.core import get_buildinfo
from osc.core import http_GET
from osc.core import http_POST
from osc.core import makeurl
from osc.core import Request

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)
from osclib.stagingapi import StagingAPI

#
# XXX - Ugly Hack. Because the way that osc import plugings we need to
# declare some functions and objects used in the decorator as global
#
global cPickle
global deepcopy
global datetime
global fcntl
global shelve
global tempfile
global wraps

global Graph
global Package_

global memoize

global build
global last_build_success
global builddepinfo
global jobhistory


class Graph(dict):
    """Graph object. Inspired in NetworkX data model."""

    def __init__(self):
        """Initialize an empty graph."""
        #  The nodes are stored in the Graph dict itself, but the
        #  adjacent list is stored as an attribute.
        self.adj = {}

    def add_node(self, name, value):
        """Add a node in the graph."""
        self[name] = value
        if name not in self.adj:
            self.adj[name] = set()

    def add_nodes_from(self, nodes_and_values):
        """Add multiple nodes"""
        for node, value in nodes_and_values:
            self.add_node(node, value)

    def add_edge(self, u, v, directed=True):
        """Add the edge u -> v, an v -> u if not directed."""
        self.adj[u].add(v)
        if not directed:
            self.adj[v].add(u)

    def add_edges_from(self, edges, directed=True):
        """Add the edges from an iterator."""
        for u, v in edges:
            self.add_edge(u, v, directed)

    def remove_edge(self, u, v, directed=True):
        """Remove the edge u -> v, an v -> u if not directed."""
        try:
            self.adj[u].remove(v)
        except KeyError:
            pass
        if not directed:
            try:
                self.adj[v].remove(u)
            except KeyError:
                pass

    def remove_edges_from(self, edges, directed=True):
        """Remove the edges from an iterator."""
        for u, v in edges:
            self.remove_edge(u, v, directed)

    def edges(self, v):
        """Get the adjancent list for a vertex."""
        return sorted(self.adj[v]) if v in self else ()

    def edges_to(self, v):
        """Get the all the vertex that point to v."""
        return sorted(u for u in self.adj if v in self.adj[u])

    def cycles(self):
        """Detect cycles using Tarjan algorithm."""
        index = [0]
        path = []
        cycles = []

        v_index = {}
        v_lowlink = {}

        def scc(node, v):
            v_index[v], v_lowlink[v] = index[0], index[0]
            index[0] += 1
            path.append(node)

            for succ in self.adj.get(node, []):
                w = self[succ]
                if w not in v_index:
                    scc(succ, w)
                    v_lowlink[v] = min(v_lowlink[v], v_lowlink[w])
                elif succ in path:
                    v_lowlink[v] = min(v_lowlink[v], v_index[w])

            if v_index[v] == v_lowlink[v]:
                i = path.index(node)
                path[:], cycle = path[:i], frozenset(path[i:])
                if len(cycle) > 1:
                    cycles.append(cycle)

        for node in sorted(self):
            v = self[node]
            if not getattr(v, 'index', 0):
                scc(node, v)
        return frozenset(cycles)

    # XXX - Deprecated - Remove in a future release
    def cycles_fragments(self):
        """Detect partial cycles using DFS."""
        cycles = set()
        visited = set()
        path = []

        def dfs(node):
            if node in visited:
                return

            visited.add(node)
            path.append(node)
            for succ in self.adj.get(node, []):
                try:
                    i = path.index(succ)
                except ValueError:
                    i = None
                if i is not None:
                    cycle = path[i:]
                    cycles.add(frozenset(cycle))
                else:
                    dfs(succ)
            path.pop()

        for node in sorted(self):
            dfs(node)
        return frozenset(cycles)


class Package_(object):
    """Simple package container. Used in a graph as a vertex."""

    def __init__(self, pkg=None, src=None, deps=None, subs=None, element=None):
        self.pkg = pkg
        self.src = src
        self.deps = deps
        self.subs = subs
        if element:
            self.load(element)

    def load(self, element):
        """Load a node from a ElementTree package XML element"""
        self.pkg = element.attrib['name']
        self.src = [e.text for e in element.findall('source')]
        assert len(self.src) == 1, 'There are more that one source packages in the graph'
        self.src = self.src[0]
        self.deps = set(e.text for e in element.findall('pkgdep'))
        self.subs = set(e.text for e in element.findall('subpkg'))

    def __repr__(self):
        return 'PKG: %s\nSRC: %s\nDEPS: %s\n SUBS: %s' % (self.pkg, self.src, self.deps, self.subs)

TMPDIR = '/var/cache/repo-checker'  # Where the cache files are stored


def memoize(ttl=None):
    """Decorator function to implement a persistent cache.

    >>> @memoize()
    ... def test_func(a):
    ...     return a

    Internally, the memoized function has a cache:

    >>> cache = [c.cell_contents for c in test_func.func_closure if 'sync' in dir(c.cell_contents)][0]
    >>> 'sync' in dir(cache)
    True

    There is a limit of the size of the cache

    >>> for k in cache:
    ...     del cache[k]
    >>> len(cache)
    0

    >>> for i in range(4095):
    ...     test_func(i)
    ... len(cache)
    4095

    >>> test_func(0)
    0

    >>> len(cache)
    4095

    >>> test_func(4095)
    4095

    >>> len(cache)
    3072

    >>> test_func(0)
    0

    >>> len(cache)
    3073

    >>> from datetime import timedelta
    >>> k = [k for k in cache if cPickle.loads(k) == ((0,), {})][0]
    >>> t, v = cache[k]
    >>> t = t - timedelta(days=10)
    >>> cache[k] = (t, v)
    >>> test_func(0)
    0
    >>> t2, v = cache[k]
    >>> t != t2
    True

    """

    # Configuration variables
    SLOTS = 4096            # Number of slots in the cache file
    NCLEAN = 1024           # Number of slots to remove when limit reached
    TIMEOUT = 60*60*2       # Time to live for every cache slot (seconds)

    def _memoize(f):
        # Implement a POSIX lock / unlock extension for shelves. Inspired
        # on ActiveState Code recipe #576591
        def _lock(filename):
            lckfile = open(filename + '.lck', 'w')
            fcntl.flock(lckfile.fileno(), fcntl.LOCK_EX)
            return lckfile

        def _unlock(lckfile):
            fcntl.flock(lckfile.fileno(), fcntl.LOCK_UN)
            lckfile.close()

        def _open_cache(cache_name):
            lckfile = _lock(cache_name)
            cache = shelve.open(cache_name, protocol=-1)
            # Store a reference to the lckfile to avoid to be closed by gc
            cache.lckfile = lckfile
            return cache

        def _close_cache(cache):
            cache.close()
            _unlock(cache.lckfile)

        def _clean_cache(cache):
            len_cache = len(cache)
            if len_cache >= SLOTS:
                nclean = NCLEAN + len_cache - SLOTS
                keys_to_delete = sorted(cache, key=lambda k: cache[k][0])[:nclean]
                for key in keys_to_delete:
                    del cache[key]

        @wraps(f)
        def _f(*args, **kwargs):
            def total_seconds(td):
                return (td.microseconds + (td.seconds + td.days * 24 * 3600.) * 10**6) / 10**6
            now = datetime.now()
            key = cPickle.dumps((args, kwargs), protocol=-1)
            updated = False
            cache = _open_cache(cache_name)
            if key in cache:
                timestamp, value = cache[key]
                updated = True if total_seconds(now-timestamp) < ttl else False
            if not updated:
                value = f(*args, **kwargs)
                cache[key] = (now, value)
            _clean_cache(cache)
            _close_cache(cache)
            return value

        cache_dir = os.path.expanduser(TMPDIR)
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir)
        cache_name = os.path.join(cache_dir, f.__name__)
        return _f

    ttl = ttl if ttl else TIMEOUT
    return _memoize


@memoize()
def build(apiurl, project, repo, arch, package):
    root = None
    try:
        url = makeurl(apiurl, ['build', project, repo, arch, package])
        root = http_GET(url).read()
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
    return root


@memoize()
def last_build_success(apiurl, src_project, tgt_project, src_package, rev):
    root = None
    try:
        url = makeurl(apiurl,
                      ['build', src_project,
                       '_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s' % (
                           quote_plus(src_package),
                           quote_plus(tgt_project),
                           rev)])
        root = http_GET(url).read()
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
    return root


@memoize(ttl=60*60*6)
def builddepinfo(apiurl, project, repository, arch):
    root = None
    try:
        print('Generating _builddepinfo for (%s, %s, %s)' % (project, repository, arch))
        url = makeurl(apiurl, ['/build/%s/%s/%s/_builddepinfo' % (project, repository, arch)])
        root = http_GET(url).read()
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
    return root


def get_project_repos(apiurl, src_project, tgt_project, src_package, rev):
    """Read the repositories of the project from _meta."""
    # XXX TODO - Shitty logic here. A better proposal is refactorize
    # _check_repo_buildsuccess.
    repos = []
    url = makeurl(apiurl,
                  ['build', src_project,
                   '_result?lastsuccess&package=%s&pathproject=%s&srcmd5=%s' % (
                       quote_plus(src_package),
                       quote_plus(tgt_project),
                       rev)])
    try:
        root = ET.parse(http_GET(url)).getroot()
        for element in root.findall('repository'):
            archs = [(e.get('arch'), e.get('result')) for e in element.findall('arch')]
            repos.append((element.get('name'), archs))
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
    return repos


def old_md5(apiurl, src_project, tgt_project, src_package, rev):
    """Recollect old MD5 for a package."""
    # XXX TODO - instead of fixing the limit, use endtime to makes
    # sure that we have the correct time frame.
    limit = 20
    query = {
        'package': src_package,
        # 'code': 'succeeded',
        'limit': limit,
    }

    repositories = get_project_repos(apiurl, src_project, tgt_project,
                                     src_package, rev)

    md5_set = set()
    for repository, archs in repositories:
        for arch, status in archs:
            if md5_set:
                break
            if status not in ('succeeded', 'outdated'):
                continue
        
            url = makeurl(apiurl, ['build', src_project, repository, arch, '_jobhistory'],
                          query=query)
            print url
            try:
                root = ET.parse(http_GET(url)).getroot()
                md5_set = set(e.get('srcmd5') for e in root.findall('jobhist'))
            except urllib2.HTTPError, e:
                print('ERROR in URL %s [%s]' % (url, e))

    return md5_set


def _check_repo_change_review_state(self, opts, id_, newstate, message='', supersed=None):
    """Taken from osc/osc/core.py, improved:
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
    url = makeurl(opts.apiurl, ['request', str(id_)], query=query)
    try:
        f = http_POST(url, data=message)
        root = ET.parse(f).getroot()
        code = root.attrib['code']
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
    return code


def _check_repo_find_submit_request(self, opts, project, package):
    xpath = "(action/target/@project='%s' and "\
            "action/target/@package='%s' and "\
            "action/@type='submit' and "\
            "(state/@name='new' or state/@name='review' or "\
            "state/@name='accepted'))" % (project, package)
    try:
        url = makeurl(opts.apiurl, ['search', 'request'], 'match=%s' % quote_plus(xpath))
        f = http_GET(url)
        collection = ET.parse(f).getroot()
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
        return None
    for root in collection.findall('request'):
        r = Request()
        r.read(root)
        return int(r.reqid)
    return None


def _check_repo_avoid_wrong_friends(self, prj, repo, arch, pkg, opts):
    xml = build(opts.apiurl, prj, repo, arch, pkg)
    if xml:
        root = ET.fromstring(xml)
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
        print('DECLINED', msg)
        self._check_repo_change_review_state(opts, id_, 'declined', message=msg)
        return []

    act = actions[0]
    type_ = act.get('type')
    if type_ != 'submit':
        msg = 'Unchecked request type %s' % type_
        print 'ACCEPTED', msg
        self._check_repo_change_review_state(opts, id_, 'accepted', message=msg)
        return []

    pkg = act.find('source').get('package')
    prj = act.find('source').get('project')
    rev = act.find('source').get('rev')
    tprj = act.find('target').get('project')
    tpkg = act.find('target').get('package')

    subm_id = 'SUBMIT(%d):' % id_
    print '%s %s/%s -> %s/%s' % (subm_id, prj, pkg, tprj, tpkg)

    packs = []
    p = CheckRepoPackage()
    p.spackage = pkg
    p.sproject = prj
    p.tpackage = tpkg
    p.tproject = tprj
    p.group = opts.grouped.get(id_, id_)
    p.request = id_

    # Get source information about the SR:
    #   - Source MD5
    #   - Entries (.tar.gz, .changes, .spec ...) and MD5
    try:
        url = makeurl(opts.apiurl, ['source', prj, pkg, '?expand=1&rev=%s' % rev])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError, e:
        print 'ERROR in URL %s [%s]' % (url, e)
        return []
    p.rev = root.attrib['srcmd5']

    # Recover the .spec files
    specs = [en.attrib['name'][:-5] for en in root.findall('entry')
             if en.attrib['name'].endswith('.spec')]

    # source checker validated it exists
    specs.remove(tpkg)
    packs.append(p)
    # Validate the rest of the spec files
    for spec in specs:
        lprj, lpkg, lmd5 = '', '', ''
        try:
            url = makeurl(opts.apiurl, ['source', prj, spec, '?expand=1'])
            root = ET.parse(http_GET(url)).getroot()
            link = root.find('linkinfo')
            if link is not None:
                lprj = link.attrib.get('project', '')
                lpkg = link.attrib.get('package', '')
                lmd5 = link.attrib['srcmd5']
        except urllib2.HTTPError:
            pass  # leave lprj

        if lprj != prj or lpkg != pkg and not p.updated:
            msg = '%s/%s should _link to %s/%s' % (prj, spec, prj, pkg)
            print 'DECLINED', msg
            self._check_repo_change_review_state(opts, id_, 'declined', message=msg)
            p.updated = True

        if lmd5 != p.rev and not p.updated:
            if lmd5 not in old_md5(opts.apiurl, lprj, p.tproject, spec, p.rev):
                msg = '%s/%s is a link but has a different md5sum than %s?' % (prj, spec, pkg)
            else:
                msg = '%s is no longer the submitted version, please resubmit HEAD' % spec
            print '[DECLINED] CHECK MANUALLY', msg
            # self._check_repo_change_review_state(opts, id_, 'declined', message=msg)
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
    root_xml = last_build_success(opts.apiurl, p.sproject, p.tproject, p.spackage, p.rev)
    root = ET.fromstring(root_xml)
    if not root:
        return False
    if 'code' in root.attrib:
        print ET.tostring(root)
        return False

    result = False
    p.goodrepos = []
    missings = {}
    alldisabled = True
    foundbuilding = None
    foundfailed = None

    tocheckrepos = []
    for repo in root.findall('repository'):
        archs = [a.attrib['arch'] for a in repo.findall('arch')]
        foundarchs = len([a for a in archs if a in ('i586', 'x86_64')])
        if foundarchs == 2:
            tocheckrepos.append(repo)

    if not tocheckrepos:
        msg = 'Missing i586 and x86_64 in the repo list'
        print msg
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        # Next line not needed, but for documentation
        p.updated = True
        return False

    for repo in tocheckrepos:
        isgood = True
        founddisabled = False
        r_foundbuilding = None
        r_foundfailed = None
        r_missings = {}
        for arch in repo.findall('arch'):
            if arch.attrib['arch'] not in ('i586', 'x86_64'):
                continue
            if 'missing' in arch.attrib:
                for pkg in arch.attrib['missing'].split(','):
                    if not self._check_repo_avoid_wrong_friends(p.sproject, repo.attrib['name'], arch.attrib['arch'], pkg, opts):
                        missings[pkg] = 1
            if not (arch.attrib['result'] in ['succeeded', 'excluded']):
                isgood = False
            if arch.attrib['result'] == 'excluded' and arch.attrib['arch'] == 'x86_64':
                p.build_excluded = True
            if arch.attrib['result'] == 'disabled':
                founddisabled = True
            if arch.attrib['result'] == 'failed' or arch.attrib['result'] == 'unknown':
                # Sometimes an unknown status is equivalent to
                # disabled, but we map it as failed to have a human
                # check (no autoreject)
                r_foundfailed = repo.attrib['name']
            if arch.attrib['result'] == 'building':
                r_foundbuilding = repo.attrib['name']
            if arch.attrib['result'] == 'outdated':
                msg = "%s's sources were changed after submissions and the old sources never built. Please resubmit" % p.spackage
                print 'DECLINED', msg
                self._check_repo_change_review_state(opts, p.request, 'declined', message=msg)
                # Next line is not needed, but for documentation
                p.updated = True
                return False

        r_missings = r_missings.keys()
        for pkg in r_missings:
            missings[pkg] = 1
        if not founddisabled:
            alldisabled = False
        if isgood:
            p.goodrepos.append(repo.attrib['name'])
            result = True
        if r_foundbuilding:
            foundbuilding = r_foundbuilding
        if r_foundfailed:
            foundfailed = r_foundfailed

    p.missings = sorted(missings)

    if result:
        return True

    if alldisabled:
        msg = '%s is disabled or does not build against factory. Please fix and resubmit' % p.spackage
        print 'DECLINED', msg
        self._check_repo_change_review_state(opts, p.request, 'declined', message=msg)
        # Next line not needed, but for documentation
        p.updated = True
        return False
    if foundbuilding:
        msg = '%s is still building for repository %s' % (p.spackage, foundbuilding)
        print msg
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        # Next line not needed, but for documentation
        p.updated = True
        return False
    if foundfailed:
        msg = '%s failed to build in repository %s - not accepting' % (p.spackage, foundfailed)
        # failures might be temporary, so don't autoreject but wait for a human to check
        print msg
        self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
        # Next line not needed, but for documentation
        p.updated = True
        return False

    return True


def _check_repo_repo_list(self, prj, repo, arch, pkg, opts, ignore=False):
    url = makeurl(opts.apiurl, ['build', prj, repo, arch, pkg])
    files = []
    try:
        binaries = ET.parse(http_GET(url)).getroot()
        for bin_ in binaries.findall('binary'):
            fn = bin_.attrib['filename']
            mt = int(bin_.attrib['mtime'])
            result = re.match(r'(.*)-([^-]*)-([^-]*)\.([^-\.]+)\.rpm', fn)
            if not result:
                if fn == 'rpmlint.log':
                    files.append((fn, '', '', mt))
                continue
            pname = result.group(1)
            if pname.endswith('-debuginfo') or pname.endswith('-debuginfo-32bit'):
                continue
            if pname.endswith('-debugsource'):
                continue
            if result.group(4) == 'src':
                continue
            files.append((fn, pname, result.group(4), mt))
    except urllib2.HTTPError:
        pass
        # if not ignore:
        #     print 'ERROR in URL %s [%s]' % (url, e)
    return files


def _check_repo_get_binary(self, apiurl, prj, repo, arch, package, file, target, mtime):
    if os.path.exists(target):
        # we need to check the mtime too as the file might get updated
        cur = os.path.getmtime(target)
        if cur > mtime:
            return
    get_binary_file(apiurl, prj, repo, arch, file, package=package, target_filename=target)


def _get_verifymd5(self, p, rev):
    try:
        url = makeurl(self.get_api_url(), ['source', p.sproject, p.spackage, '?view=info&rev=%s' % rev])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError, e:
        print 'ERROR in URL %s [%s]' % (url, e)
        return []
    return root.attrib['verifymd5']


def _checker_compare_disturl(self, disturl, p):
    distmd5 = os.path.basename(disturl).split('-')[0]
    if distmd5 == p.rev:
        return True

    vrev1 = self._get_verifymd5(p, p.rev)
    vrev2 = self._get_verifymd5(p, distmd5)
    if vrev1 == vrev2:
        return True
    print 'ERROR Revision missmatch: %s, %s' % (vrev1, vrev2)
    return False


def _check_repo_download(self, p, opts):
    p.downloads = dict()

    if p.build_excluded:
        return set()

    for repo in p.goodrepos:
        # we can assume x86_64 is there
        todownload = []
        for fn in self._check_repo_repo_list(p.sproject, repo, 'x86_64', p.spackage, opts):
            todownload.append(('x86_64', fn[0], fn[3]))

        # now fetch -32bit packs
        #for fn in self._check_repo_repo_list(p.sproject, repo, 'i586', p.spackage, opts):
        #    if fn[2] == 'x86_64':
        #        todownload.append(('i586', fn[0], fn[3]))

        p.downloads[repo] = []
        for arch, fn, mt in todownload:
            repodir = os.path.join(opts.downloads, p.spackage, repo)
            if not os.path.exists(repodir):
                os.makedirs(repodir)
            t = os.path.join(repodir, fn)
            self._check_repo_get_binary(opts.apiurl, p.sproject, repo,
                                        arch, p.spackage, fn, t, mt)
            p.downloads[repo].append(t)
            if fn.endswith('.rpm'):
                pid = subprocess.Popen(['rpm', '--nosignature', '--queryformat', '%{DISTURL}', '-qp', t],
                                       stdout=subprocess.PIPE, close_fds=True)
                os.waitpid(pid.pid, 0)[1]
                disturl = pid.stdout.readlines()[0]

                if not self._checker_compare_disturl(disturl, p):
                    p.error = '[%s] %s does not match revision %s' % (p, disturl, p.rev)
                    return set()

    toignore = set()
    for fn in self._check_repo_repo_list(p.tproject, 'standard', 'x86_64', p.tpackage, opts, ignore=True):
        toignore.add(fn[1])

    # now fetch -32bit pack list
    for fn in self._check_repo_repo_list(p.tproject, 'standard', 'i586', p.tpackage, opts, ignore=True):
        if fn[2] == 'x86_64':
            toignore.add(fn[1])
    return toignore


def _get_buildinfo(self, opts, prj, repo, arch, pkg):
    """Get the build info for a package"""
    xml = get_buildinfo(opts.apiurl, prj, pkg, repo, arch)
    root = ET.fromstring(xml)
    return [e.attrib['name'] for e in root.findall('bdep')]


def _get_builddepinfo(self, opts, prj, repo, arch, pkg):
    """Get the builddep info for a single package"""
    root = ET.fromstring(builddepinfo(opts.apiurl, prj, repo, arch))
    packages = [Package_(element=e) for e in root.findall('package')]
    package = [p for p in packages if p.pkg == pkg]
    return package[0] if package else None


# Store packages prevoiusly ignored. Don't pollute the screen.
global _ignore_packages
_ignore_packages = set()


def _get_builddepinfo_graph(self, opts, project='openSUSE:Factory', repository='standard', arch='x86_64'):
    """Generate the buildepinfo graph for a given architecture."""

    _IGNORE_PREFIX = ('texlive-', 'master-boot-code')

    # Note, by default generate the graph for all Factory. If you only
    # need the base packages you can use:
    #   project = 'Base:System'
    #   repository = 'openSUSE_Factory'

    root = ET.fromstring(builddepinfo(opts.apiurl, project, repository, arch))
    # Reset the subpackages dict here, so for every graph is a
    # different object.
    packages = [Package_(element=e) for e in root.findall('package')]

    # XXX - Ugly Exception. We need to ignore branding packages and
    # packages that one of his dependencies do not exist. Also ignore
    # preinstall images.
    packages = [p for p in packages if not ('branding' in p.pkg or p.pkg.startswith('preinstallimage-'))]

    graph = Graph()
    graph.add_nodes_from((p.pkg, p) for p in packages)

    subpkgs = {}    # Given a subpackage, recover the source package
    for p in packages:
        # Check for packages that provides the same subpackage
        for subpkg in p.subs:
            if subpkg in subpkgs:
                # print 'Subpackage duplication %s - %s (subpkg: %s)' % (p.pkg, subpkgs[subpkg], subpkg)
                pass
            else:
                subpkgs[subpkg] = p.pkg

    for p in packages:
        # Calculate the missing deps
        deps = [d for d in p.deps if 'branding' not in d]
        missing = [d for d in deps if not d.startswith(_IGNORE_PREFIX) and d not in subpkgs]
        if missing:
            if p.pkg not in _ignore_packages:
                # print 'Ignoring package. Missing dependencies %s -> (%s) %s...' % (p.pkg, len(missing), missing[:5])
                _ignore_packages.add(p.pkg)
            continue

        # XXX - Ugly Hack. Subpagackes for texlive are not correctly
        # generated. If the dependency starts with texlive- prefix,
        # assume that the correct source package is texlive.
        graph.add_edges_from((p.pkg, subpkgs[d] if not d.startswith('texlive-') else 'texlive')
                             for d in deps if not d.startswith('master-boot-code'))

    # Store the subpkgs dict in the graph. It will be used later.
    graph.subpkgs = subpkgs
    return graph


def _get_builddepinfo_cycles(self, opts, package='openSUSE:Factory', repository='standard', arch='x86_64'):
    """Generate the buildepinfo cycle list for a given architecture."""
    root = ET.fromstring(builddepinfo(opts.apiurl, package, repository, arch))
    return frozenset(frozenset(e.text for e in cycle.findall('package'))
                     for cycle in root.findall('cycle'))


def _check_repo_group(self, id_, reqs, opts):
    print '\nCheck group', reqs
    if not all(self._check_repo_buildsuccess(r, opts) for r in reqs):
        return

    # all succeeded
    toignore = set()
    destdir = os.path.expanduser('~/co/%s' % str(reqs[0].group))
    fetched = dict((r, False) for r in opts.groups.get(id_, []))
    packs = []

    for p in reqs:
        i = self._check_repo_download(p, opts)
        if p.error:
            if not p.updated:
                print p.error
                self._check_repo_change_review_state(opts, p.request, 'new', message=p.error)
                p.updated = True
            else:
                print p.error
            return
        toignore.update(i)
        fetched[p.request] = True
        packs.append(p)

    for req, f in fetched.items():
        if not f:
            packs.extend(self._check_repo_fetch_request(req, opts))
    for p in packs:
        if fetched[p.request]:
            continue
        # we need to call it to fetch the good repos to download
        # but the return value is of no interest right now
        self._check_repo_buildsuccess(p, opts)
        i = self._check_repo_download(p, opts)
        if p.error:
            print 'ERROR (ALREADY ACEPTED?):', p.error
            p.updated = True
        toignore.update(i)

    # Detect cycles - We create the full graph from _builddepinfo.
    for arch in ('x86_64',):
        factory_graph = self._get_builddepinfo_graph(opts, arch=arch)
        factory_cycles = factory_graph.cycles()
        # This graph will be updated for every request
        current_graph = deepcopy(factory_graph)

        subpkgs = current_graph.subpkgs

        # Recover all packages at once, ignoring some packages that
        # can't be found in x86_64 architecture.
        #
        # The first filter is to remove some packages that do not have
        # `goodrepos`. Thouse packages are usually marks as 'p.update
        # = True' (meaning that they are declined or there is a new
        # updated review.
        all_packages = [self._get_builddepinfo(opts, p.sproject, p.goodrepos[0], arch, p.spackage)
                        for p in packs if not p.updated]
        all_packages = [pkg for pkg in all_packages if pkg]

        subpkgs.update(dict((p, pkg.pkg) for pkg in all_packages for p in pkg.subs))

        for pkg in all_packages:
            # Update the current graph and see if we have different cycles
            edges_to = ()
            if pkg.pkg in current_graph:
                current_graph[pkg.pkg] = pkg
                current_graph.remove_edges_from(set((pkg.pkg, p) for p in current_graph.edges(pkg.pkg)))
                edges_to = current_graph.edges_to(pkg.pkg)
                current_graph.remove_edges_from(set((p, pkg.pkg) for p in edges_to))
            else:
                current_graph.add_node(pkg.pkg, pkg)
            current_graph.add_edges_from((pkg.pkg, subpkgs[p]) for p in pkg.deps if p in subpkgs)
            current_graph.add_edges_from((p, pkg.pkg) for p in edges_to
                                         if pkg.pkg in set(subpkgs[sp] for sp in current_graph[p].deps if sp in subpkgs))

        for cycle in current_graph.cycles():
            if cycle not in factory_cycles:
                print
                print 'New cycle detected:', sorted(cycle)
                factory_edges = set((u, v) for u in cycle for v in factory_graph.edges(u) if v in cycle)
                current_edges = set((u, v) for u in cycle for v in current_graph.edges(u) if v in cycle)
                print 'New edges:', sorted(current_edges - factory_edges)
                # Mark all packages as updated, to avoid to be accepted
                for p in reqs:
                    p.updated = True

    for p in reqs:
        smissing = []
        for package in p.missings:
            alreadyin = False
            # print package, packs
            for t in packs:
                if package == t.tpackage:
                    alreadyin = True
            if alreadyin:
                continue
            # print package, packs, downloads, toignore
            request = self._check_repo_find_submit_request(opts, p.tproject, package)
            if request:
                greqs = opts.groups.get(p.group, [])
                if request in greqs:
                    continue
                package = '%s(rq%s)' % (package, request)
            smissing.append(package)
        if len(smissing):
            msg = 'Please make sure to wait before these depencencies are in %s: %s' % (p.tproject, ', '.join(smissing))
            if not p.updated:
                self._check_repo_change_review_state(opts, p.request, 'new', message=msg)
                print msg
                p.updated = True
            else:
                print msg
            return

    # Create a temporal file for the params
    params_file = tempfile.NamedTemporaryFile(delete=False)
    params_file.write('\n'.join(f for f in toignore if f.strip()))
    params_file.close()

    reposets = []

    if len(packs) == 1:
        p = packs[0]
        for r in p.downloads.keys():
            reposets.append([(p, r, p.downloads[r])])
    else:
        # TODO: for groups we just pick the first repo - we'd need to create a smart
        # matrix
        dirstolink = []
        for p in packs:
            keys = p.downloads.keys()
            if not keys:
                continue
            r = keys[0]
            dirstolink.append((p, r, p.downloads[r]))
        reposets.append(dirstolink)

    if len(reposets) == 0:
        print 'NO REPOS'
        return

    for dirstolink in reposets:
        if os.path.exists(destdir):
            shutil.rmtree(destdir)
        os.makedirs(destdir)
        for p, repo, downloads in dirstolink:
            dir = destdir + '/%s' % p.tpackage
            for d in downloads:
                if not os.path.exists(dir):
                    os.mkdir(dir)
                os.symlink(d, os.path.join(dir, os.path.basename(d)))

        repochecker = os.path.join(self.repocheckerdir, 'repo-checker.pl')
        civs = "LC_ALL=C perl %s '%s' -r %s -f %s" % (repochecker, destdir, self.repodir, params_file.name)
        # print civs
        # continue
        # exit(1)
        p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
        # ret = os.waitpid(p.pid, 0)[1]
        stdoutdata, stderrdata = p.communicate()
        ret = p.returncode
        # print ret, stdoutdata, stderrdata
        if not ret:  # skip the others
            for p, repo, downloads in dirstolink:
                p.goodrepo = repo
            break
    os.unlink(params_file.name)

    updated = {}

    if ret:
        # print stdoutdata, set(map(lambda x: x.request, reqs))

        for p in reqs:
            if updated.get(p.request, False) or p.updated:
                continue
            print stdoutdata
            self._check_repo_change_review_state(opts, p.request, 'new', message=stdoutdata)
            p.updated = True
            updated[p.request] = 1
        return
    for p in reqs:
        if updated.get(p.request, False) or p.updated:
            continue
        msg = 'Builds for repo %s' % p.goodrepo
        print 'ACCEPTED', msg
        self._check_repo_change_review_state(opts, p.request, 'accepted', message=msg)
        p.updated = True
        updated[p.request] = 1
    shutil.rmtree(destdir)


def _check_repo_fetch_request(self, id_, opts):
    url = makeurl(opts.apiurl, ['request', str(id_)])
    root = ET.parse(http_GET(url)).getroot()
    return self._check_repo_one_request(root, opts)


@cmdln.alias('check', 'cr')
@cmdln.option('-s', '--skip', action='store_true', help='skip review')
def do_check_repo(self, subcmd, opts, *args):
    """${cmd_name}: Checker review of submit requests.

    Usage:
       ${cmd_name} [SRID]...
           Shows pending review requests and their current state.
    ${cmd_option_list}
    """

    opts.mode = ''

    opts.verbose = False

    opts.apiurl = self.get_api_url()
    api = StagingAPI(opts.apiurl)

    # grouped = { id: staging, }
    opts.grouped = {}
    for prj in api.get_staging_projects():
        meta = api.get_prj_pseudometa(prj)
        for req in meta['requests']:
            opts.grouped[req['id']] = prj
        for req in api.list_requests_in_prj(prj):
            opts.grouped[req] = prj

    # groups = { staging: [ids,], }
    opts.groups = {}
    for req, prj in opts.grouped.items():
        group = opts.groups.get(prj, [])
        group.append(req)
        opts.groups[prj] = group

    opts.downloads = os.path.expanduser('~/co/downloads')

    if opts.skip:
        if not len(args):
            raise oscerr.WrongArgs('Please give, if you want to skip a review specify a SRID')
        for id_ in args:
            msg = 'skip review'
            print 'ACCEPTED', msg
            self._check_repo_change_review_state(opts, id_, 'accepted', message=msg)
        return

    ids = [arg for arg in args if arg.isdigit()]

    packs = []
    if not ids:
        # xpath query, using the -m, -r, -s options
        where = "@by_user='factory-repo-checker'+and+@state='new'"
        url = makeurl(opts.apiurl, ['search', 'request'],
                      "match=state/@name='review'+and+review[%s]" % where)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            packs.extend(self._check_repo_one_request(rq, opts))
    else:
        # we have a list, use them.
        for id_ in ids:
            packs.extend(self._check_repo_fetch_request(id_, opts))

    # Order the packs before grouping
    packs = sorted(packs, key=lambda p: p.request, reverse=True)

    groups = {}
    for p in packs:
        a = groups.get(p.group, [])
        a.append(p)
        groups[p.group] = a

    self.repocheckerdir = os.path.dirname(os.path.realpath(os.path.expanduser('~/.osc-plugins/osc-check_repo.py')))
    self.repodir = "%s/repo-%s-%s-x86_64" % (TMPDIR, 'openSUSE:Factory', 'standard')
    if not os.path.exists(self.repodir):
        os.mkdir(self.repodir)
    civs = 'LC_ALL=C perl %s/bs_mirrorfull --nodebug https://build.opensuse.org/build/%s/%s/x86_64 %s' % (
        self.repocheckerdir,
        'openSUSE:Factory',
        'standard', self.repodir)
    os.system(civs)

    # Sort the groups, from high to low. This put first the stating
    # projects also
    for id_, reqs in sorted(groups.items(), reverse=True):
        self._check_repo_group(id_, reqs, opts)
