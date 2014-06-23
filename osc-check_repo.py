#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc check_repo --help' to see the usage.

from collections import defaultdict
from collections import namedtuple
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


def _check_repo_repo_list(self, prj, repo, arch, pkg, opts):
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


def _download_and_check_disturl(self, request, todownload, opts):
    for _project, _repo, arch, fn, mt in todownload:
        repodir = os.path.join(DOWNLOADS, request.src_package, _project, _repo)
        if not os.path.exists(repodir):
            os.makedirs(repodir)
        t = os.path.join(repodir, fn)
        # print 'Downloading ...', _project, _repo, arch, request.src_package, fn, t, mt
        self._check_repo_get_binary(opts.apiurl, _project, _repo,
                                    arch, request.src_package, fn, t, mt)

        request.downloads[_repo].append(t)
        if fn.endswith('.rpm'):
            pid = subprocess.Popen(['rpm', '--nosignature', '--queryformat', '%{DISTURL}', '-qp', t],
                                   stdout=subprocess.PIPE, close_fds=True)
            os.waitpid(pid.pid, 0)[1]
            disturl = pid.stdout.readlines()[0]

            if not self._checker_compare_disturl(disturl, request):
                request.error = '[%s] %s does not match revision %s' % (request, disturl, request.srcmd5)


def _check_repo_download(self, request, opts):
    request.downloads = defaultdict(list)

    if request.build_excluded:
        return set()

    ToDownload = namedtuple('ToDownload', ('project', 'repo', 'arch', 'package', 'size'))

    for repo in request.goodrepos:
        # we can assume x86_64 is there
        todownload = [ToDownload(request.src_project, repo, 'x86_64', fn[0], fn[3])
                      for fn in self._check_repo_repo_list(request.src_project,
                                                           repo,
                                                           'x86_64',
                                                           request.src_package,
                                                           opts)]

        self._download_and_check_disturl(request, todownload, opts)
        if request.error:
            return set()

    if 'openSUSE:Factory:Staging:' in str(request.group):
        todownload = [
            ToDownload(request.group, 'standard', 'x86_64', fn[0], fn[3])
            for fn in self._check_repo_repo_list(request.group,
                                                 'standard',
                                                 'x86_64',
                                                 request.src_package,
                                                 opts)]

        self._download_and_check_disturl(request, todownload, opts)
        if request.error:
            return set()

        todownload = [
            ToDownload(request.group + ':DVD', 'standard', 'x86_64', fn[0], fn[3])
            for fn in self._check_repo_repo_list(request.group + ':DVD',
                                                 'standard',
                                                 'x86_64',
                                                 request.src_package,
                                                 opts)]

        self._download_and_check_disturl(request, todownload, opts)
        if request.error:
            return set()

    toignore = set()
    for fn in self._check_repo_repo_list(request.tgt_project, 'standard', 'x86_64', request.tgt_package, opts):
        if fn[1]:
            toignore.add(fn[1])

    # now fetch -32bit pack list
    for fn in self._check_repo_repo_list(request.tgt_project, 'standard', 'i586', request.tgt_package, opts):
        if fn[1] and fn[2] == 'x86_64':
            toignore.add(fn[1])
    return toignore


def _get_buildinfo(self, opts, prj, repo, arch, pkg):
    """Get the build info for a package"""
    xml = get_buildinfo(opts.apiurl, prj, pkg, repo, arch)
    root = ET.fromstring(xml)
    return [e.attrib['name'] for e in root.findall('bdep')]


# Used in _check_repo_group only to cache error messages
_errors_printed = set()


def _check_repo_group(self, id_, requests, opts):
    print '\nCheck group', requests

    if not all(self.checkrepo.is_buildsuccess(r) for r in requests):
        return

    toignore = set()
    destdir = os.path.expanduser('~/co/%s' % str(requests[0].group))
    fetched = dict((r, False) for r in opts.groups.get(id_, []))
    packs = []

    for request in requests:
        i = self._check_repo_download(request, opts)
        if request.error and request.error not in _errors_printed:
            _errors_printed.add(request.error)
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

    # Extend packs array with the packages and .spec files of the
    # not-fetched requests.  The not fetched ones are the requests of
    # the same group that are not listed as a paramater.
    for request_id, is_fetched in fetched.items():
        if not is_fetched:
            packs.extend(self.checkrepo.check_specs(request_id=request_id))

    # Download the repos from the request of the same group not
    # explicited in the command line.
    for rq in packs:
        if fetched[rq.request_id]:
            continue
        # we need to call it to fetch the good repos to download
        # but the return value is of no interest right now
        self.checkrepo.is_buildsuccess(rq)
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

    # If a package is in a Stagin Project, it will have in
    # request.downloads an entry for 'standard' (the repository of a
    # Staging Project) Also in this same field there will be another
    # valid repository (probably openSUSE_Factory)
    #
    # We want to test with the Perl script the binaries of one of the
    # repos, and if fail test the other repo.  The order of testing
    # will be stored in the execution_plan.

    execution_plan = defaultdict(list)

    # Get all the repos where at least there is a package
    all_repos = set()
    for rq in packs:
        all_repos.update(rq.downloads)

    if len(all_repos) == 0:
        print 'NO REPOS'
        return

    for rq in packs:
        for _repo in all_repos:
            if _repo in rq.downloads:
                execution_plan[_repo].append((rq, _repo, rq.downloads[_repo]))
            else:
                _other_repo = [r for r in rq.downloads if r != _repo]
                _other_repo = _other_repo[0]  # XXX TODO - Recurse here to create combinations
                execution_plan[_repo].append((rq, _other_repo, rq.downloads[_other_repo]))

    repo_checker_error = None
    for _repo, dirstolink in execution_plan.items():
        if os.path.exists(destdir):
            shutil.rmtree(destdir)
        os.makedirs(destdir)
        for rq, repo, downloads in dirstolink:
            dir = destdir + '/%s' % rq.tgt_package
            for d in downloads:
                if not os.path.exists(dir):
                    os.mkdir(dir)
                os.symlink(d, os.path.join(dir, os.path.basename(d)))

        repochecker = os.path.join(self.plugin_dir, 'repo-checker.pl')
        civs = "LC_ALL=C perl %s '%s' -r %s -f %s" % (repochecker, destdir, self.repo_dir, params_file.name)
        p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
        stdoutdata, stderrdata = p.communicate()
        ret = p.returncode

        # There are several execution plans, each one can have its own
        # error message.  We are only interested in the error related
        # with the staging project (_repo == 'standard').  If we need
        # to report one, we will report this one.
        if _repo == 'standard':
            repo_checker_error = stdoutdata

        # print ret, stdoutdata, stderrdata
        # raise Exception()

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
            if repo_checker_error not in _errors_printed:
                _errors_printed.add(repo_checker_error)
                print repo_checker_error
            self.checkrepo.change_review_state(rq.request_id, 'new', message=repo_checker_error)
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


def mirror_full(plugin_dir, repo_dir):
    """Call bs_mirrorfull script to mirror packages."""
    url = 'https://build.opensuse.org/build/%s/%s/x86_64' % ('openSUSE:Factory', 'standard')

    if not os.path.exists(repo_dir):
        os.mkdir(repo_dir)

    script = 'LC_ALL=C perl %s/bs_mirrorfull --nodebug %s %s' % (plugin_dir, url, repo_dir)
    os.system(script)


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

    # Group the requests into staging projects (or alone if is an
    # isolated request)
    #
    # For example:
    # {
    #     'openSUSE:Factory:Staging:J': [235851, 235753],
    #     235856: [235856],
    # }
    #
    # * The list of requests is not the full list of requests in this
    #   group / staging project, but only the ones listed as a
    #   paramenter.
    #
    # * The full list of requests can be found in
    #   self.checkrepo.groups['openSUSE:Factory:Staging:J']
    #
    groups = {}
    for request in requests:
        rqs = groups.get(request.group, [])
        rqs.append(request)
        groups[request.group] = rqs

    # Mirror the packages locally in the CACHEDIR
    plugin = '~/.osc-plugins/osc-check_repo.py'
    self.plugin_dir = os.path.dirname(os.path.realpath(os.path.expanduser(plugin)))
    self.repo_dir = '%s/repo-%s-%s-x86_64' % (CACHEDIR, 'openSUSE:Factory', 'standard')
    mirror_full(self.plugin_dir, self.repo_dir)

    # Sort the groups, from high to low. This put first the stating
    # projects also
    for id_, reqs in sorted(groups.items(), reverse=True):
        self._check_repo_group(id_, reqs, opts)
