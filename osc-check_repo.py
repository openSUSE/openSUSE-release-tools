#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc check_repo --help' to see the usage.

import os
import re
import shutil
import subprocess
import tempfile
from urllib import quote_plus
import urllib2
import sys
from xml.etree import cElementTree as ET

from osc import oscerr
from osc import cmdln

from osc.core import get_binary_file
from osc.core import get_buildinfo
from osc.core import http_GET
from osc.core import makeurl
from osc.core import Request

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)
from osclib.checkrepo import CheckRepo
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR


# Directory where download binary packages.
DOWNLOADS = os.path.expanduser('~/co/downloads')


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
    xml = self.checkrepo.build(prj, repo, arch, pkg)
    if xml:
        root = ET.fromstring(xml)
        for binary in root.findall('binary'):
            # if there are binaries, we're out
            return False
    return True


def _check_repo_buildsuccess(self, request, opts):
    root_xml = self.checkrepo.last_build_success(request.src_project, request.tgt_project, request.src_package, request.srcmd5)
    root = ET.fromstring(root_xml)
    if not root:
        return False
    if 'code' in root.attrib:
        print ET.tostring(root)
        return False

    result = False
    request.goodrepos = []
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
        self.checkrepo.change_review_state(request.request_id, 'new', message=msg)
        # Next line not needed, but for documentation
        request.updated = True
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
                    if not self._check_repo_avoid_wrong_friends(request.src_project, repo.attrib['name'], arch.attrib['arch'], pkg, opts):
                        missings[pkg] = 1
            if arch.attrib['result'] not in ('succeeded', 'excluded'):
                isgood = False
            if arch.attrib['result'] == 'excluded' and arch.attrib['arch'] == 'x86_64':
                request.build_excluded = True
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
                msg = "%s's sources were changed after submissions and the old sources never built. Please resubmit" % request.src_package
                print 'DECLINED', msg
                self.checkrepo.change_review_state(request.request_id, 'declined', message=msg)
                # Next line is not needed, but for documentation
                request.updated = True
                return False

        r_missings = r_missings.keys()
        for pkg in r_missings:
            missings[pkg] = 1
        if not founddisabled:
            alldisabled = False
        if isgood:
            request.goodrepos.append(repo.attrib['name'])
            result = True
        if r_foundbuilding:
            foundbuilding = r_foundbuilding
        if r_foundfailed:
            foundfailed = r_foundfailed

    request.missings = sorted(missings)

    if result:
        return True

    if alldisabled:
        msg = '%s is disabled or does not build against factory. Please fix and resubmit' % request.src_package
        print 'DECLINED', msg
        self.checkrepo.change_review_state(request.request_id, 'declined', message=msg)
        # Next line not needed, but for documentation
        request.updated = True
        return False
    if foundbuilding:
        msg = '%s is still building for repository %s' % (request.src_package, foundbuilding)
        print msg
        self.checkrepo.change_review_state(request.request_id, 'new', message=msg)
        # Next line not needed, but for documentation
        request.updated = True
        return False
    if foundfailed:
        msg = '%s failed to build in repository %s - not accepting' % (request.src_package, foundfailed)
        # failures might be temporary, so don't autoreject but wait for a human to check
        print msg
        self.checkrepo.change_review_state(request.request_id, 'new', message=msg)
        # Next line not needed, but for documentation
        request.updated = True
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


def _get_verifymd5(self, request, rev):
    try:
        url = makeurl(self.get_api_url(), ['source', request.src_project, request.src_package, '?view=info&rev=%s' % rev])
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError, e:
        print 'ERROR in URL %s [%s]' % (url, e)
        return []
    return root.attrib['verifymd5']


def _checker_compare_disturl(self, disturl, request):
    distmd5 = os.path.basename(disturl).split('-')[0]
    if distmd5 == request.srcmd5:
        return True

    vrev1 = self._get_verifymd5(request, request.srcmd5)
    vrev2 = self._get_verifymd5(request, distmd5)
    if vrev1 == vrev2:
        return True
    print 'ERROR Revision missmatch: %s, %s' % (vrev1, vrev2)
    return False


def _check_repo_download(self, request, opts):
    request.downloads = dict()

    if request.build_excluded:
        return set()

    for repo in request.goodrepos:
        # we can assume x86_64 is there
        todownload = []
        for fn in self._check_repo_repo_list(request.src_project, repo, 'x86_64', request.src_package, opts):
            todownload.append(('x86_64', fn[0], fn[3]))

        # now fetch -32bit packs
        # for fn in self._check_repo_repo_list(p.sproject, repo, 'i586', p.spackage, opts):
        #    if fn[2] == 'x86_64':
        #        todownload.append(('i586', fn[0], fn[3]))

        request.downloads[repo] = []
        for arch, fn, mt in todownload:
            repodir = os.path.join(DOWNLOADS, request.src_package, repo)
            if not os.path.exists(repodir):
                os.makedirs(repodir)
            t = os.path.join(repodir, fn)
            self._check_repo_get_binary(opts.apiurl, request.src_project, repo,
                                        arch, request.src_package, fn, t, mt)
            request.downloads[repo].append(t)
            if fn.endswith('.rpm'):
                pid = subprocess.Popen(['rpm', '--nosignature', '--queryformat', '%{DISTURL}', '-qp', t],
                                       stdout=subprocess.PIPE, close_fds=True)
                os.waitpid(pid.pid, 0)[1]
                disturl = pid.stdout.readlines()[0]

                if not self._checker_compare_disturl(disturl, request):
                    request.error = '[%s] %s does not match revision %s' % (request, disturl, request.srcmd5)
                    return set()

    toignore = set()
    for fn in self._check_repo_repo_list(request.tgt_project, 'standard', 'x86_64', request.tgt_package, opts, ignore=True):
        toignore.add(fn[1])

    # now fetch -32bit pack list
    for fn in self._check_repo_repo_list(request.tgt_project, 'standard', 'i586', request.tgt_package, opts, ignore=True):
        if fn[2] == 'x86_64':
            toignore.add(fn[1])
    return toignore


def _get_buildinfo(self, opts, prj, repo, arch, pkg):
    """Get the build info for a package"""
    xml = get_buildinfo(opts.apiurl, prj, pkg, repo, arch)
    root = ET.fromstring(xml)
    return [e.attrib['name'] for e in root.findall('bdep')]


def _check_repo_group(self, id_, requests, opts):
    print '\nCheck group', requests
    if not all(self._check_repo_buildsuccess(r, opts) for r in requests):
        return

    # all succeeded
    toignore = set()
    destdir = os.path.expanduser('~/co/%s' % str(requests[0].group))
    fetched = dict((r, False) for r in opts.groups.get(id_, []))
    packs = []

    for request in requests:
        i = self._check_repo_download(request, opts)
        if request.error:
            if not request.updated:
                print request.error
                self.checkrepo.change_review_state(request.request_id, 'new', message=request.error)
                request.updated = True
            else:
                print request.error
            return
        toignore.update(i)
        fetched[request.request_id] = True
        packs.append(request)

    for request_id, f in fetched.items():
        if not f:
            packs.extend(self.checkrepo.check_specs(request_id=request_id))
    for rq in packs:
        if fetched[rq.request_id]:
            continue
        # we need to call it to fetch the good repos to download
        # but the return value is of no interest right now
        self._check_repo_buildsuccess(rq, opts)
        i = self._check_repo_download(rq, opts)
        if rq.error:
            print 'ERROR (ALREADY ACEPTED?):', rq.error
            rq.updated = True
        toignore.update(i)

    # Detect cycles into the current Factory graph after we update the
    # links with the current list of request.
    cycle_detector = CycleDetector(opts.apiurl)
    for (cycle, new_edges) in cycle_detector.cycles(requests=packs):
        print
        print 'New cycle detected:', sorted(cycle)
        print 'New edges:', new_edges
        # Mark all packages as updated, to avoid to be accepted
        for request in requests:
            request.updated = True

    for rq in requests:
        smissing = []
        for package in rq.missings:
            alreadyin = False
            # print package, packs
            for t in packs:
                if package == t.tgt_package:
                    alreadyin = True
            if alreadyin:
                continue
            # print package, packs, downloads, toignore
            request = self._check_repo_find_submit_request(opts, rq.tgt_project, package)
            if request:
                greqs = opts.groups.get(rq.group, [])
                if request in greqs:
                    continue
                package = '%s(rq%s)' % (package, request)
            smissing.append(package)
        if len(smissing):
            msg = 'Please make sure to wait before these depencencies are in %s: %s' % (rq.tgt_project, ', '.join(smissing))
            if not rq.updated:
                self.checkrepo.change_review_state(rq.request_id, 'new', message=msg)
                print msg
                rq.updated = True
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
        for rq in packs:
            keys = rq.downloads.keys()
            if not keys:
                continue
            r = keys[0]
            dirstolink.append((rq, r, rq.downloads[r]))
        reposets.append(dirstolink)

    if len(reposets) == 0:
        print 'NO REPOS'
        return

    for dirstolink in reposets:
        if os.path.exists(destdir):
            shutil.rmtree(destdir)
        os.makedirs(destdir)
        for rq, repo, downloads in dirstolink:
            dir = destdir + '/%s' % rq.tgt_package
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
        # print stdoutdata, set(map(lambda x: x.request_id, reqs))

        for rq in requests:
            if updated.get(rq.request_id, False) or rq.updated:
                continue
            print stdoutdata
            self.checkrepo.change_review_state(rq.request_id, 'new', message=stdoutdata)
            p.updated = True
            updated[rq.request_id] = 1
        return
    for rq in requests:
        if updated.get(rq.request_id, False) or rq.updated:
            continue
        msg = 'Builds for repo %s' % rq.goodrepo
        print 'ACCEPTED', msg
        self.checkrepo.change_review_state(rq.request_id, 'accepted', message=msg)
        rq.updated = True
        updated[rq.request_id] = 1
    shutil.rmtree(destdir)


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

    self.checkrepo = CheckRepo(opts.apiurl)

    # XXX TODO - Remove this the all access to opt.group[s|ed] comes
    # from checkrepo.
    opts.grouped = self.checkrepo.grouped
    opts.groups = self.checkrepo.groups

    if opts.skip:
        if not len(args):
            raise oscerr.WrongArgs('Provide #IDs to skip.')

        for id_ in args:
            msg = 'skip review'
            print 'ACCEPTED', msg
            self.checkrepo.change_review_state(id_, 'accepted', message=msg)
        return

    ids = [arg for arg in args if arg.isdigit()]

    # Store requests' package information and .spec files: store all
    # source containers involved.
    requests = []
    if not ids:
        # Return a list, we flat here with .extend()
        for request in self.checkrepo.pending_requests():
            requests.extend(self.checkrepo.check_specs(request=request))
    else:
        # We have a list, use them.
        for request_id in ids:
            requests.extend(self.checkrepo.check_specs(request_id=request_id))

    # Order the packs before grouping
    requests = sorted(requests, key=lambda p: p.request_id, reverse=True)

    groups = {}
    for request in requests:
        a = groups.get(request.group, [])
        a.append(request)
        groups[request.group] = a

    self.repocheckerdir = os.path.dirname(os.path.realpath(os.path.expanduser('~/.osc-plugins/osc-check_repo.py')))
    self.repodir = "%s/repo-%s-%s-x86_64" % (CACHEDIR, 'openSUSE:Factory', 'standard')
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
