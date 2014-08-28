# Copyright (C) 2014 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc check_repo --help' to see the usage.

from collections import defaultdict
from collections import namedtuple
import os
import shutil
import subprocess
import tempfile
import sys

from osc import oscerr
from osc import cmdln


# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)
from osclib.checkrepo import CheckRepo
from osclib.cycle import CycleDetector
from osclib.memoize import CACHEDIR


def _check_repo_download(self, request):
    request.downloads = defaultdict(list)

    # Found cached version for the request, but the cached can be
    # partial. For example, in a rebuild we can have locally a working
    # package. So the download list needs to be updated with the local
    # copies.
    if request.is_partially_cached:
        request.downloads = self.checkrepo._get_downloads_from_local(request)

    if request.build_excluded:
        return set()

    ToDownload = namedtuple('ToDownload', ('project', 'repo', 'arch', 'package', 'size'))

    for i, goodrepo in enumerate(request.goodrepos):
        repo = goodrepo[1]

        # we can assume x86_64 is there
        todownload = [ToDownload(request.shadow_src_project, repo, 'x86_64',
                                 fn[0], fn[3]) for fn in
                      self.checkrepo.get_package_list_from_repository(
                          request.shadow_src_project, repo, 'x86_64',
                          request.src_package)]

        self.checkrepo._download(request, todownload)
        if request.error:
            return set()

    staging_prefix = 'openSUSE:{}:Staging:'.format(self.checkrepo.opensuse)
    if staging_prefix in str(request.group):
        todownload = [ToDownload(request.group, 'standard', 'x86_64',
                                 fn[0], fn[3]) for fn in
                      self.checkrepo.get_package_list_from_repository(
                          request.group, 'standard', 'x86_64',
                          request.src_package)]

        self.checkrepo._download(request, todownload)
        if request.error:
            return set()

        todownload = [ToDownload(request.group + ':DVD', 'standard',
                                 'x86_64', fn[0], fn[3]) for fn in
                      self.checkrepo.get_package_list_from_repository(
                          request.group + ':DVD', 'standard',
                          'x86_64', request.src_package)]

        self.checkrepo._download(request, todownload)
        if request.error:
            return set()
    return self.checkrepo._toignore(request)


# Used in _check_repo_group only to cache error messages
_errors_printed = set()


def _check_repo_group(self, id_, requests, debug = False):
    print '> Check group [%s]' % ', '.join(r.str_compact() for r in requests)

    if not all(self.checkrepo.is_buildsuccess(r) for r in requests if r.action_type != 'delete'):
        return

    toignore = set()
    destdir = os.path.expanduser('~/co/%s' % str(requests[0].group))
    fetched = dict((r, False) for r in self.checkrepo.groups.get(id_, []))
    packs = []

    for request in requests:
        if request.action_type == 'delete':
            continue

        i = self._check_repo_download(request)
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

    packs.extend(requests)

    # Extend packs array with the packages and .spec files of the
    # not-fetched requests.  The not fetched ones are the requests of
    # the same group that are not listed as a paramater.
    for request_id, is_fetched in fetched.items():
        if not is_fetched:
            packs.extend(self.checkrepo.check_specs(request_id=request_id))

    # Download the repos from the request of the same group not
    # explicited in the command line.
    for rq in packs:
        if rq.request_id in fetched and fetched[rq.request_id]:
            continue
        i = set()
        if rq.action_type == 'delete':
            # for delete requests we care for toignore
            i = self.checkrepo._toignore(rq)
            # We also check that nothing depends on the package and
            # that the request originates by the package maintainer
            if not self.checkrepo.is_secure_to_delete(rq):
                rq.error = 'This request is not secure to remove. Check dependencies or author.'
                print ' - %s' % rq.error
                rq.updated = True
        else:
            # we need to call it to fetch the good repos to download
            # but the return value is of no interest right now.
            self.checkrepo.is_buildsuccess(rq)
            i = self._check_repo_download(rq)
            if rq.error:
                print 'ERROR (ALREADY ACEPTED?):', rq.error
                rq.updated = True

        toignore.update(i)

    # Detect cycles into the current Factory / openSUSE graph after we
    # update the links with the current list of request.
    cycle_detector = CycleDetector(self.checkrepo.staging)
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
            request = self.checkrepo.find_request_id(rq.tgt_project, package)
            if request:
                greqs = self.checkrepo.groups.get(rq.group, [])
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

    DEBUG_PLAN = debug

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
            if self.checkrepo.check_disturl(rq, md5_disturl=disturl):
                all_good_downloads[(prj, repo)].add(disturl)
                if DEBUG_PLAN:
                    print 'DEBUG Good DISTURL -', rq.str_compact(), (prj, repo), disturl
            elif DEBUG_PLAN:
                print 'DEBUG Bad DISTURL -', rq.str_compact(), (prj, repo), disturl

    if not all_good_downloads:
        print ' - No matching downloads for disturl found.'
        if len(packs) == 1 and packs[0].src_package in ('rpmlint-tests'):
            print " - %s known to have no installable rpms, skipped"%packs[0].src_package
        return

    for project, repo in all_good_downloads:
        plan = (project, repo)
        valid_disturl = all_good_downloads[plan]
        if DEBUG_PLAN:
            print 'DEBUG Designing plan', plan, valid_disturl
        for rq in packs:
            if DEBUG_PLAN:
                print 'DEBUG In', rq
            # Find (project, repo) in rq.downloads.
            keys = [key for key in rq.downloads if key[0] == project and key[1] == repo and key[2] in valid_disturl]
            if DEBUG_PLAN:
                print 'DEBUG Keys', keys

            if keys:
                assert len(keys) == 1, 'Found more that one download candidate for the same (project, repo)'
                execution_plan[plan].append((rq, plan, rq.downloads[keys[0]]))
                if DEBUG_PLAN:
                    print 'DEBUG Downloads', rq.downloads[keys[0]]
            else:
                if DEBUG_PLAN:
                    print 'DEBUG Searching for a fallback!'
                fallbacks = [key for key in rq.downloads if (key[0], key[1]) in all_good_downloads and key[2] in all_good_downloads[(key[0], key[1])]]
                if fallbacks:
                    # XXX TODO - Recurse here to create combinations
                    # Meanwhile, I will priorize the one fallback that is in a staging project.
                    fallbacks_from_staging = [fb for fb in fallbacks if 'Staging' in fb[0]]
                    fallbacks = fallbacks_from_staging if fallbacks_from_staging else fallbacks
                    fallback = fallbacks.pop()
                    if DEBUG_PLAN:
                        print 'DEBUG Fallback found', fallback
                        print 'DEBUG Fallback downloads', rq.downloads[fallback]

                    alternative_plan = fallback[:2]
                    execution_plan[plan].append((rq, alternative_plan, rq.downloads[fallback]))
                # elif rq.status == 'succeeded':
                else:
                    print 'no fallback for', rq

    repo_checker_error = ''
    for project_repo in execution_plan:
        dirstolink = execution_plan[project_repo]

        if DEBUG_PLAN:
            print 'DEBUG Running plan', project_repo
            for rq, repo, downloads in dirstolink:
                print ' ', rq
                print ' ', repo
                for f in downloads:
                    print '   -', f

        # Makes sure to remove the directory is case of early exit.
        if os.path.exists(destdir):
            shutil.rmtree(destdir)

        os.makedirs(destdir)
        for rq, _, downloads in dirstolink:
            dir_ = destdir + '/%s' % rq.tgt_package
            for d in downloads:
                if not os.path.exists(dir_):
                    os.mkdir(dir_)
                target = os.path.join(dir_, os.path.basename(d))
                if os.path.exists(target):
                    print 'Warning, symlink already exists', d, target
                    os.unlink(target)
                os.symlink(d, target)

        repochecker = os.path.join(self.plugin_dir, 'repo-checker.pl')
        civs = "LC_ALL=C perl %s '%s' -r %s -f %s" % (repochecker, destdir, self.repo_dir, params_file.name)
        p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
        stdoutdata, stderrdata = p.communicate()
        stdoutdata = stdoutdata.strip()
        ret = p.returncode

        # Clean the directory that contains all the symlinks
        shutil.rmtree(destdir)

        # There are several execution plans, each one can have its own
        # error message.
        if ret:
            print ' - Execution plan for %s failed'%project_repo
        else:
            print ' - Successful plan', project_repo

        if stdoutdata:
            print '-' * 40
            print stdoutdata
            print '-' * 40
        if stderrdata:
            print '-' * 40
            print stderrdata
            print '-' * 40

        # Detect if this error message comes from a staging project.
        # Store it in the repo_checker_error, that is the text that
        # will be published in the error message.
        staging_prefix = 'openSUSE:{}:Staging:'.format(self.checkrepo.opensuse)
        if staging_prefix in project_repo[0]:
            repo_checker_error = stdoutdata
        if not any(staging_prefix in p_r[0] for p_r in execution_plan):
            repo_checker_error += '\nExecution plan: %s\n%s' % ('/'.join(project_repo), stdoutdata)

        # print ret, stdoutdata, stderrdata
        # raise Exception()

        if not ret:  # skip the others
            for p, gr, downloads in dirstolink:
                p.goodrepo = '%s/%s' % gr
            break

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
        self.checkrepo.remove_link_if_shadow_devel(rq)
        rq.updated = True
        updated[rq.request_id] = 1


def _mirror_full(self, plugin_dir, repo_dir):
    """Call bs_mirrorfull script to mirror packages."""
    url = 'https://build.opensuse.org/build/openSUSE:%s/%s/x86_64' % (self.checkrepo.opensuse, 'standard')

    if not os.path.exists(repo_dir):
        os.mkdir(repo_dir)

    script = 'LC_ALL=C perl %s/bs_mirrorfull --nodebug %s %s' % (plugin_dir, url, repo_dir)
    os.system(script)


def _print_request_and_specs(self, request_and_specs):
    print request_and_specs[0]
    for spec in request_and_specs[1:]:
        print ' *', spec


@cmdln.alias('check', 'cr')
@cmdln.option('-p', '--project', dest='project', metavar='PROJECT', default='Factory',
              help='select a different project instead of openSUSE:Factory')
@cmdln.option('-s', '--skip', action='store_true', help='skip review')
@cmdln.option('-n', '--dry', action='store_true', help='dry run, don\'t change review state')
@cmdln.option('-v', '--verbose', action='store_true', help='verbose output')
def do_check_repo(self, subcmd, opts, *args):
    """${cmd_name}: Checker review of submit requests.

    Usage:
       ${cmd_name} [SRID]...
           Shows pending review requests and their current state.
       ${cmd_name} PRJ
           Shows pending review requests in a specific project.
    ${cmd_option_list}
    """

    self.checkrepo = CheckRepo(self.get_api_url(), opts.project, readonly = opts.dry, debug=opts.verbose)

    if opts.skip:
        if not len(args):
            raise oscerr.WrongArgs('Provide #IDs to skip.')

        for id_ in args:
            msg = 'skip review'
            print 'ACCEPTED', msg
            self.checkrepo.change_review_state(id_, 'accepted', message=msg)
            _request = self.checkrepo.get_request(id_, internal=True)
            self.checkrepo.remove_link_if_shadow_devel(_request)
        return

    prjs = [arg for arg in args if not arg.isdigit()]
    ids = [arg for arg in args if arg.isdigit()]

    # Recover the requests that are for this project and expand ids.
    for prj in prjs:
        prj = self.checkrepo.staging.prj_from_letter(prj)
        meta = self.checkrepo.staging.get_prj_pseudometa(prj)
        ids.extend(rq['id'] for rq in meta['requests'])

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
    self.repo_dir = '%s/repo-%s-%s-x86_64' % (CACHEDIR, 'openSUSE:{}'.format(opts.project), 'standard')
    self._mirror_full(self.plugin_dir, self.repo_dir)

    print
    print 'Analysis results'
    print '----------------'
    print

    # Sort the groups, from high to low. This put first the stating
    # projects also
    for id_, reqs in sorted(groups.items(), reverse=True):
        self._check_repo_group(id_, reqs, debug=opts.verbose)
        print
        print
