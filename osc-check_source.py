#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc checker --help' to see the usage.


import os
import re
import shutil
import subprocess
import sys
import urllib2
from xml.etree import cElementTree as ET

from osc.core import checkout_package
from osc.core import http_GET
from osc.core import http_POST
# from osc.core import link_pac
from osc.core import makeurl
# from osc.core import show_upstream_rev_vrev
from osc import cmdln

# Expand sys.path to search modules inside the pluging directory
PLUGINDIR = os.path.dirname(os.path.realpath(__file__.replace('.pyc', '.py')))
sys.path.append(PLUGINDIR)
from osclib.conf import Config
from osclib.stagingapi import StagingAPI


# For a description of this decorator, visit
#  http://www.imdb.com/title/tt0067756/
def _silent_running(fn):
    def _fn(*args, **kwargs):
        _stdout = sys.stdout
        sys.stdout = open(os.devnull, 'wb')
        try:
            result = fn(*args, **kwargs)
        finally:
            sys.stdout = _stdout
        return result
    return _fn

checkout_pkg = _silent_running(checkout_package)


def _checker_parse(self, apiurl, project, package, revision=None,
                   brief=False, verbose=False):
    query = {'view': 'info', 'parse': 1}
    if revision:
        query['rev'] = revision
    url = makeurl(apiurl, ['source', project, package], query)

    ret = {'name': None, 'version': None}

    try:
        xml = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError, e:
        print('ERROR in URL %s [%s]' % (url, e))
        return ret

    # ET's boolean check is screwed
    if xml.find('name') is not None:
        ret['name'] = xml.find('name').text

    if xml.find('version') is not None:
        ret['version'] = xml.find('version').text

    return ret


def _checker_change_review_state(self, opts, id_, newstate,
                                 by_group='', by_user='', message='',
                                 supersed=None):
    """ taken from osc/osc/core.py, improved:
        - verbose option added,
        - empty by_user=& removed.
        - numeric id_ can be int().
    """
    query = {'cmd': 'changereviewstate', 'newstate': newstate}
    if by_group:
        query['by_group'] = by_group
    if by_user:
        query['by_user'] = by_user
    if supersed:
        query['superseded_by'] = supersed
    url = makeurl(opts.apiurl, ['request', str(id_)], query=query)
    if opts.dry_run:
        print 'dry-run: change review state: %s' % url
        return
    root = ET.parse(http_POST(url, data=message)).getroot()
    return root.attrib['code']


def _checker_prepare_dir(self, dir):
    olddir = os.getcwd()
    os.chdir(dir)
    shutil.rmtree(".osc")
    os.chdir(olddir)


def _checker_add_review(self, opts, id_, by_group=None, by_user=None,
                        msg=None):
    query = {'cmd': 'addreview'}
    if by_group:
        query['by_group'] = by_group
    elif by_user:
        query['by_user'] = by_user
    else:
        raise Exception('we need either by_group or by_user')

    url = makeurl(opts.apiurl, ['request', str(id_)], query)
    if opts.dry_run:
            print 'dry-run: adding review %s' % url
            return 0

    try:
        r = http_POST(url, data=msg)
    except urllib2.HTTPError:
        return 1

    code = ET.parse(r).getroot().attrib['code']
    if code != 'ok':
        raise Exception(r)
    return 0


def _checker_add_review_team(self, opts, id_):
    return self._checker_add_review(opts, id_, by_group='opensuse-review-team', msg="Please review sources")


def _checker_get_verifymd5(self, opts, src_project, src_package, rev=None):
    query = {
        'view': 'info',

    }
    if rev:
        query['rev'] = rev
    url = makeurl(opts.apiurl, ('source', src_project, src_package), query=query)
    try:
        root = ET.parse(http_GET(url)).getroot()
    except urllib2.HTTPError:
        return None

    if root is not None:
        srcmd5 = root.get('verifymd5')
        return srcmd5


def _checker_is_srcmd5_in_factory(self, opts, src_package, srcmd5):
    if not srcmd5:
        return False

    factory_srcmd5 = self._checker_get_verifymd5(opts, 'openSUSE:Factory',
                                                 src_package)
    return srcmd5 == factory_srcmd5


def _checker_accept_request(self, opts, id_, msg, diff=10000):
    if diff > 8:
        self._checker_add_review_team(opts, id_)
    # else:
    #     self._checker_add_review(opts, id_, by_user='coolo', msg='Does it look harmless?')

    self._checker_add_review(opts, id_, by_user='factory-repo-checker', msg='Please review build success')

    self._checker_change_review_state(opts, id_, 'accepted', by_group='factory-auto', message=msg)
    print("accepted " + msg)


class FakeAction(object):
    def __init__(self, src_project, src_package, tgt_project, tgt_package, src_rev):
        self.src_project = src_project
        self.src_package = src_package
        self.tgt_project = tgt_project
        self.tgt_package = tgt_package
        self.src_rev = src_rev


def _checker_one_request(self, rq, opts):
    if (opts.verbose):
        ET.dump(rq)
        print(opts)
    id_ = int(rq.get('id'))
    act_id = 0
    actions = rq.findall('action')
    if len(actions) > 1:
        msg = '2 actions in one SR is not supported - https://github.com/openSUSE/osc-plugin-factory/fork_select'
        self._checker_change_review_state(opts, id_, 'declined', by_group='factory-auto', message=msg)
        print("declined " + msg)
        return

    for act in actions:
        act_id += 1
        _type = act.get('type')
        if _type == "submit":
            pkg = act.find('source').get('package')
            prj = act.find('source').get('project')
            rev = act.find('source').get('rev')
            tprj = act.find('target').get('project')
            tpkg = act.find('target').get('package')

            # Check in the srcmd5 is actually in Factory
            srcmd5 = self._checker_get_verifymd5(opts, prj, pkg, rev=rev)
            if self._checker_is_srcmd5_in_factory(opts, pkg, srcmd5):
                msg = 'Found Factory sources'
                self._checker_change_review_state(opts, id_, 'accepted', by_group='factory-auto', message=msg)
                continue

            src = {'package': pkg, 'project': prj, 'rev': rev, 'error': None}
            e = []
            if not pkg:
                e.append('no source/package in request %d, action %d' % (id_, act_id))
            if not prj:
                e.append('no source/project in request %d, action %d' % (id_, act_id))
            if e:
                src.error = '; '.join(e)

            e = []
            if not tpkg:
                e.append('no target/package in request %d, action %d; ' % (id_, act_id))
            if not prj:
                e.append('no target/project in request %d, action %d; ' % (id_, act_id))
            # it is no error, if the target package dies not exist

            subm_id = "SUBMIT(%d):" % id_
            print ("\n%s %s/%s -> %s/%s" % (subm_id,
                                            prj,  pkg,
                                            tprj, tpkg))

            # Check if the package comes from openSUSE:Factory and
            # have a release target (like openSUSE:13.2)
            if prj == 'openSUSE:Factory' and tprj == '%s' % self.api.project:
                fake_devel_prj = '%s:Devel' % self.api.project

                # _rev, _vrev = show_upstream_rev_vrev(opts.apiurl, prj, pkg, revision=rev, expand=True)
                # assert _rev == rev, 'Revision is not the same'
                # link_pac(src_project=prj, src_package=pkg,
                #          dst_project=fake_devel_prj, dst_package=tpkg,
                #          force=True,
                #          rev=_rev, vrev=_vrev)

                act = FakeAction(prj, pkg, tprj, tpkg, rev)
                self.api.submit_to_prj(act, fake_devel_prj, force_enable_build=True)
                self._checker_accept_request(opts, id_, 'The request is linked to %s' % fake_devel_prj, diff=0)
                continue

            dpkg = self._checker_check_devel_package(opts, tprj, tpkg)
            # white list
            self._devel_projects['network:mail:zarafa/'] = 'x2go'
            self._devel_projects['devel:languages:D/'] = 'd'
            self._devel_projects['Novell:NTS'] = 'support'
            self._devel_projects['Virtualization:Appliances:Builder/'] = 'python3-kiwi'
            self._devel_projects['isv:ownCloud:desktop/'] = 'owncloud-client'
            self._devel_projects['system:snappy/'] = 'snappy'
            self._devel_projects['Application:ERP:Tryton:Factory/'] = 'tryton'
            self._devel_projects['devel:languages:pascal/'] = 'fpc'
            self._devel_projects['server:messaging/'] = 'libgloox'
            self._devel_projects['Application:Dochazka/'] = 'perl-App-Dochazka-Common'
            self._devel_projects['security:Stunnel/'] = 'stunnel'
            self._devel_projects['network:cluster/' ] = 'cluster'
            self._devel_projects['science:HPC/' ] = 'hpc'
            if dpkg:
                [dprj, dpkg] = dpkg.split('/')
            else:
                dprj = None
            if dprj and (dprj != prj or dpkg != pkg) and ('IGNORE_DEVEL_PROJECTS' not in os.environ):
                msg = "'%s/%s' is the devel package, submission is from '%s'" % (dprj, dpkg, prj)
                print("declined " + msg)
                self._checker_change_review_state(opts, id_, 'declined', by_group='factory-auto', message=msg)
                continue
            if not dprj and (prj + "/") not in self._devel_projects:
                msg = "'%s' is not a valid devel project of %s - please pick one of the existent" % (prj, tprj)
                print("declined " + msg)
                self._checker_change_review_state(opts, id_, 'declined', by_group='factory-auto', message=msg)
                continue

            dir = os.path.expanduser("~/co/%s" % str(id_))
            if os.path.exists(dir):
                print("%s already exists" % dir)
                continue
            os.mkdir(dir)
            os.chdir(dir)

            old_infos = {'version': None}
            try:
                checkout_pkg(opts.apiurl, tprj, tpkg, pathname=dir,
                             server_service_files=True, expand_link=True)
                self._checker_prepare_dir(tpkg)
                os.rename(tpkg, "_old")
                old_infos = self._checker_parse(opts.apiurl, tprj, tpkg)
            except urllib2.HTTPError:
                print("failed to checkout %s/%s" % (tprj, tpkg))

            checkout_pkg(opts.apiurl, prj, pkg, revision=rev,
                         pathname=dir, server_service_files=True, expand_link=True)
            os.rename(pkg, tpkg)
            self._checker_prepare_dir(tpkg)

            new_infos = self._checker_parse(opts.apiurl, prj, pkg, revision=rev)
            if new_infos['name'] != tpkg:
                msg = "A pkg submitted as %s has to build as 'Name: %s' - found Name '%s'" % (tpkg, tpkg, new_infos['name'])
                self._checker_change_review_state(opts, id_, 'declined', by_group='factory-auto', message=msg)
                continue

            sourcechecker = os.path.join(PLUGINDIR, 'source-checker.pl')
            civs = ""
            new_version = None
            if old_infos['version'] and old_infos['version'] != new_infos['version']:
                new_version = new_infos['version']
                civs += "NEW_VERSION='{}' ".format(new_version)
            civs += "LC_ALL=C perl %s _old %s 2>&1" % (sourcechecker, tpkg)
            p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, close_fds=True)
            ret = os.waitpid(p.pid, 0)[1]
            checked = p.stdout.readlines()

            output = '  '.join(checked).translate(None, '\033')
            os.chdir("/tmp")

            if ret != 0:
                msg = "Output of check script:\n" + output
                self._checker_change_review_state(opts, id_, 'declined', by_group='factory-auto', message=msg)
                print("declined " + msg)
                shutil.rmtree(dir)
                continue

            shutil.rmtree(dir)
            msg = 'Check script succeeded'
            if len(checked) and checked[-1].startswith('DIFFCOUNT'):
                # this is a major break through in perl<->python communication!
                diff = int(checked.pop().split(' ')[1])
                output = '  '.join(checked).translate(None, '\033')
                if not new_version:
                    diff = 12345
            else:  # e.g. new package
                diff = 13579

            if len(checked):
                msg = msg + "\n\nOutput of check script (non-fatal):\n" + output

            if self._checker_accept_request(opts, id_, msg, diff=diff):
                continue

        else:
            self._checker_change_review_state(opts, id_, 'accepted',
                                              by_group='factory-auto',
                                              message="Unchecked request type %s" % _type)


def _checker_check_devel_package(self, opts, project, package):
    if project not in self._devel_projects:
        url = makeurl(opts.apiurl, ['search', 'package'], "match=[@project='%s']" % project)
        root = ET.parse(http_GET(url)).getroot()
        for p in root.findall('package'):
            name = p.attrib['name']
            d = p.find('devel')
            if d is not None:
                dprj = d.attrib['project']
                self._devel_projects["%s/%s" % (project, name)] = "%s/%s" % (dprj, d.attrib['package'])
                # for new packages to check
                self._devel_projects[dprj + "/"] = 1
            elif not name.startswith("_product") and not name.startswith('preinstallimage') and not name == 'Test-DVD-x86_64':
                print("NO DEVEL IN", name)
            # mark we tried
            self._devel_projects[project] = 1
    key = "%s/%s" % (project, package)
    if key in self._devel_projects:
        return self._devel_projects[key]


@cmdln.option('-v', '--verbose', action='store_true', help="verbose output")
@cmdln.option('-p', '--project', dest='project', metavar='PROJECT', default='Factory',
              help='select a different project instead of openSUSE:Factory')
@cmdln.option('-n', '--dry-run', action='store_true', help="dry run")
def do_check_source(self, subcmd, opts, *args):
    """${cmd_name}: checker review of submit requests.

    Usage:
      osc check_source [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    self._devel_projects = {}
    opts.apiurl = self.get_api_url()

    Config('openSUSE:%s' % opts.project)
    self.api = StagingAPI(opts.apiurl, 'openSUSE:%s' % opts.project)

    if len(args) and args[0] == 'skip':
        for id_ in args[1:]:
            self._checker_accept_request(opts, id_, 'skip review')
        return
    ids = {}
    for a in args:
        if re.match('\d+', a):
            ids[a] = 1

    if not ids:
        review = "@by_group='factory-auto'+and+@state='new'"
        target = "@project='{}'".format(self.api.project)
        target_nf = "@project='{}:NonFree'".format(self.api.project)
        url = makeurl(opts.apiurl, ('search', 'request'),
                      "match=state/@name='review'+and+review[%s]+and+(target[%s]+or+target[%s])" % (review, target, target_nf))
        root = ET.parse(http_GET(url)).getroot()
        for rq in root.findall('request'):
            self._checker_one_request(rq, opts)
    else:
        # we have a list, use them.
        for id_ in ids:
            url = makeurl(opts.apiurl, ('request', id_))
            f = http_GET(url)
            xml = ET.parse(f)
            root = xml.getroot()
            self._checker_one_request(root, opts)
