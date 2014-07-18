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
from osclib.checkrepo import CheckRepo, DOWNLOADS
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR


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
        print " - WARNING: Can't found list of packages (RPM) for %s in %s (%s, %s)" % (pkg, prj, repo, arch)
    return files


def _check_repo_get_binary(self, apiurl, prj, repo, arch, package, file, target, mtime):
    if os.path.exists(target):
        # we need to check the mtime too as the file might get updated
        cur = os.path.getmtime(target)
        if cur > mtime:
            return

    get_binary_file(apiurl, prj, repo, arch, file, package=package, target_filename=target)


def _download(self, request, todownload, opts, src_package=None, only_rpm=False):
    """Download the packages refereced in a request."""
    downloaded = []

    last_disturl = None
    last_disturldir = None

    src_package = request.src_package if not src_package else src_package

    # We need to order the files to download.  First RPM packages (to
    # set disturl), after that the rest.

    todownload_rpm = [rpm for rpm in todownload if rpm[3].endswith('.rpm')]
    todownload_rest = [rpm for rpm in todownload if not rpm[3].endswith('.rpm')]

    for _project, _repo, arch, fn, mt in todownload_rpm:
        repodir = os.path.join(DOWNLOADS, request.src_package, _project, _repo)
        if not os.path.exists(repodir):
            os.makedirs(repodir)
        t = os.path.join(repodir, fn)
        self._check_repo_get_binary(opts.apiurl, _project, _repo,
                                    arch, src_package, fn, t, mt)

        # Organize the files into DISTURL directories.
        disturl = self.checkrepo._md5_disturl(self.checkrepo._disturl(t))
        disturldir = os.path.join(repodir, disturl)
        last_disturl, last_disturldir = disturl, disturldir
        file_in_disturl = os.path.join(disturldir, fn)
        if not os.path.exists(disturldir):
            os.makedirs(disturldir)
        try:
            os.symlink(t, file_in_disturl)
        except:
            pass
            # print 'Found previous link.'

        request.downloads[(_project, _repo, disturl)].append(file_in_disturl)
        downloaded.append(file_in_disturl)

    if only_rpm:
        return downloaded

    for _project, _repo, arch, fn, mt in todownload_rest:
        repodir = os.path.join(DOWNLOADS, request.src_package, _project, _repo)
        if not os.path.exists(repodir):
            os.makedirs(repodir)
        t = os.path.join(repodir, fn)
        self._check_repo_get_binary(opts.apiurl, _project, _repo,
                                    arch, src_package, fn, t, mt)

        file_in_disturl = os.path.join(last_disturldir, fn)
        if last_disturldir:
            try:
                os.symlink(t, file_in_disturl)
            except:
                pass
                # print 'Found previous link.'
        else:
            print "I don't know where to put", fn

        request.downloads[(_project, _repo, last_disturl)].append(file_in_disturl)
        downloaded.append(file_in_disturl)

    return downloaded


def _check_repo_toignore(self, request, opts):
    toignore = set()
    for fn in self._check_repo_repo_list(request.tgt_project, 'standard', 'x86_64', request.tgt_package, opts):
        if fn[1]:
            toignore.add(fn[1])

    # now fetch -32bit pack list
    for fn in self._check_repo_repo_list(request.tgt_project, 'standard', 'i586', request.tgt_package, opts):
        if fn[1] and fn[2] == 'x86_64':
            toignore.add(fn[1])
    return toignore


def _check_repo_download(self, request, opts):

    if request.is_cached:
        request.downloads = self.checkrepo._get_downloads_from_local(request)
        # print ' - Found cached version for', request.str_compact()
        return self._check_repo_toignore(request, opts)

    if request.build_excluded:
        return set()

    ToDownload = namedtuple('ToDownload', ('project', 'repo', 'arch', 'package', 'size'))

    for i, goodrepo in enumerate(request.goodrepos):
        repo = goodrepo[1]

        # we can assume x86_64 is there
        todownload = [ToDownload(request.src_project, repo, 'x86_64', fn[0], fn[3])
                      for fn in self._check_repo_repo_list(request.src_project,
                                                           repo,
                                                           'x86_64',
                                                           request.src_package,
                                                           opts)]

        self._download(request, todownload, opts)
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

        self._download(request, todownload, opts)
        if request.error:
            return set()

        # Download extra packages.  This will invalidate some checks.
        # To identify the packages we need to store more information
        # inside the request.
        extra_packages = self.checkrepo.extra_packages[request.group][request.request_id]
        for extra_package in extra_packages:
            todownload = [
                ToDownload(request.group, 'standard', 'x86_64', fn[0], fn[3])
                for fn in self._check_repo_repo_list(request.group,
                                                     'standard',
                                                     'x86_64',
                                                     extra_package,
                                                     opts)]

            extra_package_filenames = self._download(request, todownload, opts,
                                                     src_package=extra_package, only_rpm=True)
            request.extra_packages.extend(extra_package_filenames)

            if request.error:
                return set()

        # Download packages for subproject :DVD
        todownload = [
            ToDownload(request.group + ':DVD', 'standard', 'x86_64', fn[0], fn[3])
            for fn in self._check_repo_repo_list(request.group + ':DVD',
                                                 'standard',
                                                 'x86_64',
                                                 request.src_package,
                                                 opts)]

        self._download(request, todownload, opts)
        if request.error:
            return set()
    return self._check_repo_toignore(request, opts)


def _get_buildinfo(self, opts, prj, repo, arch, pkg):
    """Get the build info for a package"""
    xml = get_buildinfo(opts.apiurl, prj, pkg, repo, arch)
    root = ET.fromstring(xml)
    return [e.attrib['name'] for e in root.findall('bdep')]


# Used in _check_repo_group only to cache error messages
_errors_printed = set()


def _check_repo_group(self, id_, requests, opts):
    print '> Check group [%s]' % ', '.join(r.str_compact() for r in requests)

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
                print ' - %s' % request.error
                self.checkrepo.change_review_state(request.request_id, 'new', message=request.error)
                request.updated = True
            else:
                print ' - %s' % request.error
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
        i = set()
        if rq.action_type == 'delete':
            # for delete requests we only care for toignore
            i = self._check_repo_toignore(rq, opts)
        else:
            # we need to call it to fetch the good repos to download
            # but the return value is of no interest right now.
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
        print ' - New cycle detected:', sorted(cycle)
        print ' - New edges:', new_edges
        # Mark all packages as updated, to avoid to be accepted
        for request in requests:
            request.updated = True

    for rq in requests:
        smissing = []
        for package in rq.missings:
            alreadyin = False
            for t in packs:
                if package == t.tgt_package:
                    alreadyin = True
            if alreadyin:
                continue
            request = self._check_repo_find_submit_request(opts, rq.tgt_project, package)
            if request:
                greqs = opts.groups.get(rq.group, [])
                if request in greqs:
                    continue
                package = '#[%s](%s)' % (request, package)
            smissing.append(package)
        if len(smissing):
            msg = 'Please make sure to wait before these depencencies are in %s: %s' % (rq.tgt_project, ', '.join(smissing))
            if not rq.updated:
                self.checkrepo.change_review_state(rq.request_id, 'new', message=msg)
                print ' - %s' % msg
                rq.updated = True
            else:
                print ' - %s' % msg
            return

    # Create a temporal file for the params
    params_file = tempfile.NamedTemporaryFile(delete=False)
    params_file.write('\n'.join(f for f in toignore if f.strip()))
    params_file.close()

    # We want to test with the Perl script the binaries of one of the
    # repos, and if fail test the other repo.  The order of testing
    # will be stored in the execution_plan.

    execution_plan = defaultdict(list)

    # Get all the (project, repo, disturl) where the disturl is
    # compatible with the request.  For the same package we can have
    # more than one good triplet, even with different MD5 DISTRUL.
    # The general strategy is collect that the different triplets and
    # provide some execution_plans where for the same (project, repo)
    # for every package, with a fallback to a different (project,
    # repo) in case that the original is not found.
    all_good_downloads = defaultdict(set)
    for rq in packs:
        for (prj, repo, disturl) in rq.downloads:
            extra_packages = set(rq.extra_packages)
            is_extra_packages = all(download in extra_packages for download in rq.downloads[(prj, repo, disturl)])
            if is_extra_packages or self.checkrepo.check_disturl(rq, md5_disturl=disturl):
                all_good_downloads[(prj, repo)].add(disturl)
            #     print 'GOOD -', rq.str_compact(), (prj, repo), disturl
            # else:
            #     print 'BAD -', rq.str_compact(), (prj, repo), disturl

    if not all_good_downloads:
        print ' - Not good downloads found (NO REPO).'
        return

    for project, repo in all_good_downloads:
        plan = (project, repo)
        valid_disturl = all_good_downloads[plan]
        # print 'DESIGNING PLAN', plan, valid_disturl
        for rq in packs:
            # print 'IN', rq
            # Find (project, repo) in rq.downloads.
            keys = [key for key in rq.downloads
                    if key[0] == project and key[1] == repo and key[2] in valid_disturl]
            # print 'KEYS', keys

            if keys:
                # Now we can have more than one key per (project,
                # repo), because there are extra packages.
                # assert len(keys) == 1, 'Found more that one download candidate for the same (project, repo)'
                _downloads = []
                for key in keys:
                    _downloads.extend(rq.downloads[key])
                execution_plan[plan].append((rq, plan, _downloads))
                # print 'DOWNLOADS', _downloads
            else:
                # print 'FALLBACK'
                fallbacks = [key for key in rq.downloads
                             if (key[0], key[1]) in all_good_downloads and key[2] in all_good_downloads[(key[0], key[1])]]
                if fallbacks:
                    # Merge the downloads for fallbacks in case that
                    # are in the same (project, repo)
                    fallback = fallbacks.pop()

                    keys = [key for key in fallbacks if key[0] == fallback[0] and key[1] == fallback[1]]
                    keys.append(fallback)
                    _downloads = []
                    for key in keys:
                        _downloads.extend(rq.downloads[key])

                    # print 'FALLBACK TO', fallback
                    # print 'FALLBACK DOWNLOADS', _downloads

                    alternative_plan = fallback[:2]
                    execution_plan[plan].append((rq, alternative_plan, _downloads))
                # elif rq.status == 'succeeded':
                else:
                    print 'no fallback for', rq

    # raise Exception()

    repo_checker_error = ''
    for project_repo in execution_plan:
        dirstolink = execution_plan[project_repo]

        # print 'Running plan', project_repo
        # for rq, repo, downloads in dirstolink:
        #     print ' ', rq
        #     print ' ', repo
        #     for f in downloads:
        #         print '   -', f

        # continue

        if os.path.exists(destdir):
            shutil.rmtree(destdir)
        os.makedirs(destdir)
        for rq, _, downloads in dirstolink:
            dir_ = destdir + '/%s' % rq.tgt_package
            for d in downloads:
                if not os.path.exists(dir_):
                    os.mkdir(dir_)
                os.symlink(d, os.path.join(dir_, os.path.basename(d)))

        repochecker = os.path.join(self.plugin_dir, 'repo-checker.pl')
        civs = "LC_ALL=C perl %s '%s' -r %s -f %s" % (repochecker, destdir, self.repo_dir, params_file.name)
        p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
        stdoutdata, stderrdata = p.communicate()
        stdoutdata = stdoutdata.strip()
        ret = p.returncode

        # There are several execution plans, each one can have its own
        # error message.
        if ret:
            print ' - Result for execution plan', project_repo
            print '-' * 40
            print stdoutdata
            print '-' * 40
        else:
            print ' - Successful plan', project_repo

        # Detect if this error message comes from a staging project.
        # Store it in the repo_checker_error, that is the text that
        # will be published in the error message.
        if 'openSUSE:Factory:Staging:' in project_repo[0]:
            repo_checker_error = stdoutdata
        if not any('openSUSE:Factory:Staging:' in p_r[0] for p_r in execution_plan):
            repo_checker_error += '\nExecution plan: %s\n%s' % ('/'.join(project_repo), stdoutdata)

        # print ret, stdoutdata, stderrdata
        # raise Exception()

        if not ret:  # skip the others
            for p, gr, downloads in dirstolink:
                p.goodrepo = '%s/%s' % gr
            break

    # raise Exception()

    os.unlink(params_file.name)

    updated = {}

    if ret:
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


def _print_request_and_specs(self, request_and_specs):
    print request_and_specs[0]
    for spec in request_and_specs[1:]:
        print ' *', spec


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
    print 'Pending requests list:'
    print '----------------------'
    if not ids:
        # Return a list, we flat here with .extend()
        for request in self.checkrepo.pending_requests():
            request_and_specs = self.checkrepo.check_specs(request=request)
            self._print_request_and_specs(request_and_specs)
            requests.extend(request_and_specs)
    else:
        # We have a list, use them.
        for request_id in ids:
            request_and_specs = self.checkrepo.check_specs(request_id=request_id)
            self._print_request_and_specs(request_and_specs)
            requests.extend(request_and_specs)

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

    print
    print 'Analysis results'
    print '----------------'
    print

    # Sort the groups, from high to low. This put first the stating
    # projects also
    for id_, reqs in sorted(groups.items(), reverse=True):
        self._check_repo_group(id_, reqs, opts)
        print
        print
