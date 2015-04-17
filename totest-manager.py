#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import argparse
import json
import os
import re
import sys
import urllib2

from xml.etree import cElementTree as ET

import osc


# Expand sys.path to search modules inside the pluging directory
PLUGINDIR = os.path.expanduser(os.path.dirname(os.path.realpath(__file__)))
sys.path.append(PLUGINDIR)
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osc.core import makeurl


# QA Results
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3


class ToTestBase(object):
    """Base class to store the basic interface"""

    def __init__(self, project, dryrun, restart_tests):
        self.project = project
        self.dryrun = dryrun
        self.restart_tests = restart_tests
        self.api = StagingAPI(osc.conf.config['apiurl'], project='openSUSE:%s' % project)
        self.known_failures = self.known_failures_from_dashboard(project)

    def openqa_group(self):
        return self.project

    def iso_prefix(self):
        return self.project

    def jobs_num(self):
        return 90

    def binaries_of_product(self, project, product):
        url = self.api.makeurl(['build', project, 'images', 'local', product])
        try:
            f = self.api.retried_GET(url)
        except urllib2.HTTPError:
            return []

        ret = []
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            ret.append(binary.get('filename'))

        return ret

    def get_current_snapshot(self):
        """Return the current snapshot in :ToTest"""

        # for now we hardcode all kind of things
        for binary in self.binaries_of_product('openSUSE:%s:ToTest' % self.project, '_product:openSUSE-cd-mini-%s' % self.arch()):
            result = re.match(r'openSUSE-%s-NET-.*-Snapshot(.*)-Media.iso' % self.iso_prefix(),
                              binary)
            if result:
                return result.group(1)

        return None

    def find_openqa_results(self, snapshot):
        """Return the openqa jobs of a given snapshot and filter out the
        cloned jobs

        """

        url = makeurl('https://openqa.opensuse.org', ['api', 'v1', 'jobs'], { 'group': self.openqa_group(), 'build': snapshot } )
        f = self.api.retried_GET(url)
        jobs = []
        for job in json.load(f)['jobs']:
            if job['clone_id']:
                continue
            job['name'] = job['name'].replace(snapshot, '')
            jobs.append(job)
        return jobs

    def _result2str(self, result):
        if result == QA_INPROGRESS:
            return 'inprogress'
        elif result == QA_FAILED:
            return 'failed'
        else:
            return 'passed'

    def find_failed_module(self, testmodules):
        # print json.dumps(testmodules, sort_keys=True, indent=4)
        for module in testmodules:
            if module['result'] != 'failed':
                continue
            flags = module['flags']
            if 'fatal' in flags or 'important' in flags:
                return module['name']
                break
            print module['name'], module['result'], module['flags']

    def overall_result(self, snapshot):
        """Analyze the openQA jobs of a given snapshot Returns a QAResult"""

        if snapshot is None:
            return QA_FAILED

        jobs = self.find_openqa_results(snapshot)

        if len(jobs) < self.jobs_num():  # not yet scheduled
            print 'we have only %s jobs' % len(jobs)
            return QA_INPROGRESS

        number_of_fails = 0
        in_progress = False
        for job in jobs:
            # print json.dumps(job, sort_keys=True, indent=4)
            if job['result'] in ('failed', 'incomplete', 'skipped'):
                jobname = job['name'] + '@' + job['settings']['MACHINE']
                if jobname in self.known_failures:
                    self.known_failures.remove(jobname)
                    continue
                number_of_fails += 1
                # print json.dumps(job, sort_keys=True, indent=4), jobname
                failedmodule = self.find_failed_module(job['modules'])
                url = 'https://openqa.opensuse.org/tests/%s' % job['id']
                print jobname, url, failedmodule, job['retry_avbl']
                # if number_of_fails < 3: continue
                if self.restart_tests:
                    result = os.system("openqa jobs/%s/restart post" % job['id'])
                    print "Restarted job %s" % job['id']
            elif job['result'] == 'passed':
                continue
            elif job['result'] == 'none':
                if job['state'] != 'cancelled':
                    in_progress = True
            else:
                raise Exception(job['result'])

        if number_of_fails > 0:
            return QA_FAILED

        if in_progress:
            return QA_INPROGRESS

        if self.known_failures:
            print 'Some are now passing', self.known_failures
        return QA_PASSED

    def all_repos_done(self, project, codes=None):
        """Check the build result of the project and only return True if all
        repos of that project are either published or unpublished

        """

        codes = ['published', 'unpublished'] if not codes else codes

        url = self.api.makeurl(['build', project, '_result'], {'code': 'failed'})
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for repo in root.findall('result'):
            if repo.get('repository') == 'ports':
                continue
            if repo.get('dirty', '') == 'true':
                print repo.get('project'), repo.get('repository'), repo.get('arch'), 'dirty'
                return False
            if repo.get('code') not in codes:
                print repo.get('project'), repo.get('repository'), repo.get('arch'), repo.get('code')
                return False
        return True

    def maxsize_for_package(self, package):
        if re.match(r'.*-mini-.*', package):
            return 737280000  # a CD needs to match

        if re.match(r'.*-dvd5-.*', package):
            return 4700372992  # a DVD needs to match

        if re.match(r'.*-image-livecd-x11.*', package):
            return 681574400  # not a full CD

        if re.match(r'.*-image-livecd.*', package):
            return 999999999  # a GB stick

        if re.match(r'.*-dvd9-dvd-.*', package):
            return 8539996159

        if package == '_product:openSUSE-ftp-ftp-i586_x86_64':
            return None

        if package == '_product:openSUSE-Addon-NonOss-ftp-ftp-i586_x86_64':
            return None

        if package == '_product:openSUSE-ftp-ftp-ppc_ppc64_ppc64le':
            return None

        raise Exception('No maxsize for {}'.format(package))

    def package_ok(self, project, package, repository, arch):
        """Checks one package in a project and returns True if it's succeeded

        """

        query = {'package': package, 'repository': repository, 'arch': arch}

        url = self.api.makeurl(['build', project, '_result'], query)
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for repo in root.findall('result'):
            status = repo.find('status')
            if status.get('code') != 'succeeded':
                print project, package, repository, arch, status.get('code')
                return False

        maxsize = self.maxsize_for_package(package)
        if not maxsize:
            return True

        url = self.api.makeurl(['build', project, repository, arch, package])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            if not binary.get('filename', '').endswith('.iso'):
                continue
            isosize = int(binary.get('size', 0))
            if isosize > maxsize:
                print project, package, repository, arch, 'too large by %s bytes' % (isosize-maxsize)
                return False

        return True

    def factory_snapshottable(self):
        """Check various conditions required for factory to be snapshotable

        """

        if not self.all_repos_done('openSUSE:%s' % self.project):
            return False

        for product in self.ftp_products + self.main_products:
            if not self.package_ok('openSUSE:%s' % self.project, product, 'images', 'local'):
                return False

            if len(self.livecd_products):

                if not self.all_repos_done('openSUSE:%s:Live' % self.project):
                    return False

                for arch in ['i586', 'x86_64' ]:
                    for product in self.livecd_products:
                        if not self.package_ok('openSUSE:%s:Live' % self.project, product, 'standard', arch):
                            return False

        return True

    def release_package(self, project, package, set_release=None):
        query = {'cmd': 'release'}

        if set_release:
            query['setrelease'] = set_release

        baseurl = ['source', project, package]

        url = self.api.makeurl(baseurl, query=query)
        self.api.retried_POST(url)

    def update_totest(self, snapshot):
        print 'Updating snapshot %s' % snapshot
        self.api.switch_flag_in_prj('openSUSE:%s:ToTest' % self.project, flag='publish', state='disable')

        for product in self.ftp_products:
            self.release_package('openSUSE:%s' % self.project, product)

        for cd in self.livecd_products:
            self.release_package('openSUSE:%s:Live' % self.project, cd, set_release='Snapshot%s' % snapshot)

        for cd in self.main_products:
            self.release_package('openSUSE:%s' % self.project, cd, set_release='Snapshot%s' % snapshot)

    def publish_factory_totest(self):
        print 'Publish ToTest'
        self.api.switch_flag_in_prj('openSUSE:%s:ToTest' % self.project, flag='publish', state='enable')

    def totest_is_publishing(self):
        """Find out if the publishing flag is set in totest's _meta"""

        url = self.api.makeurl(['source', 'openSUSE:%s:ToTest' % self.project, '_meta'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        if not root.find('publish'):  # default true
            return True

        for flag in root.find('publish'):
            if flag.get('repository', None) or flag.get('arch', None):
                continue
            if flag.tag == 'enable':
                return True
        return False

    def totest(self):
        current_snapshot = self.get_current_snapshot()
        new_snapshot = self.current_version()

        current_result = self.overall_result(current_snapshot)

        print 'current_snapshot', current_snapshot, self._result2str(current_result)

        can_release = (current_result != QA_INPROGRESS and self.factory_snapshottable())

        # not overwriting
        if new_snapshot == current_snapshot:
            can_release = False
        elif not self.all_repos_done('openSUSE:%s:ToTest' % self.project):
            # the repos have to be done, otherwise we better not touch them with a new release
            can_release = False

        can_publish = (current_result == QA_PASSED)

        # already published
        if self.totest_is_publishing():
            can_publish = False

        if can_publish and not self.dryrun:
            self.publish_factory_totest()
            can_release = False  # we have to wait

        if can_release and not self.dryrun:
            self.update_totest(new_snapshot)

    def known_failures_from_dashboard(self, project):
        known_failures = []
        if self.project in ("Factory:PowerPC", "Factory:ARM"):
            project = "Factory"
        else:
            project = self.project

        url = self.api.makeurl(['source', 'openSUSE:%s:Staging' % project, 'dashboard', 'known_failures'])
        f = self.api.retried_GET(url)
        for line in f:
            if not line[0] == '#':
                known_failures.append(line.strip())
        return known_failures


class ToTestFactory(ToTestBase):
    main_products = ['_product:openSUSE-dvd5-dvd-i586',
                     '_product:openSUSE-dvd5-dvd-x86_64',
                     '_product:openSUSE-cd-mini-i586',
                     '_product:openSUSE-cd-mini-x86_64']

    ftp_products = ['_product:openSUSE-ftp-ftp-i586_x86_64',
                    '_product:openSUSE-Addon-NonOss-ftp-ftp-i586_x86_64']

    livecd_products = ['kiwi-image-livecd-kde',
                       'kiwi-image-livecd-gnome',
                       'kiwi-image-livecd-x11']

    def __init__(self, project, dryrun, restart_tests):
        ToTestBase.__init__(self, project, dryrun, restart_tests)

    def openqa_group(self):
        return 'openSUSE Tumbleweed'

    def iso_prefix(self):
        return 'Tumbleweed'

    def arch(self):
        return 'x86_64'

    # for Factory we check the version of the release package
    def current_version(self):
        url = self.api.makeurl(['build', 'openSUSE:%s' % self.project, 'standard', self.arch(),
                                '_product:openSUSE-release'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)
        raise Exception("can't find factory version")

class ToTestFactoryPowerPC(ToTestBase):
    main_products = ['_product:openSUSE-dvd5-BE-ppc64',
                     '_product:openSUSE-dvd5-LE-ppc64le',
                     '_product:openSUSE-cd-mini-ppc64',
                     '_product:openSUSE-cd-mini-ppc64le']

    ftp_products = [ '_product:openSUSE-ftp-ftp-ppc_ppc64_ppc64le' ]

    livecd_products = []

    def __init__(self, project, dryrun):
        ToTestBase.__init__(self, project, dryrun)

    def openqa_group(self):
        return 'openSUSE Tumbleweed PowerPC'

    def arch(self):
        return 'ppc64le'

    def iso_prefix(self):
        return 'Tumbleweed'

    def jobs_num(self):
        return 4

    # for Factory we check the version of the release package
    def current_version(self):
        url = self.api.makeurl(['build', 'openSUSE:%s' % self.project, 'standard', self.arch(),
                                '_product:openSUSE-release'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)
        raise Exception("can't find factory powerpc version")

class ToTestFactoryARM(ToTestFactory):
    main_products = [ '_product:openSUSE-cd-mini-aarch64']

    ftp_products = [ '_product:openSUSE-ftp-ftp-aarch64' ]

    livecd_products = []

    def __init__(self, project, dryrun):
        ToTestFactory.__init__(self, project, dryrun)

    def openqa_group(self):
        return 'AArch64'

    def arch(self):
        return 'aarch64'

    def jobs_num(self):
        return 4

class ToTest132(ToTestBase):
    main_products = [
        '_product:openSUSE-dvd5-dvd-i586',
        '_product:openSUSE-dvd5-dvd-x86_64',
        '_product:openSUSE-cd-mini-i586',
        '_product:openSUSE-cd-mini-x86_64',
        '_product:openSUSE-dvd5-dvd-promo-i586',
        '_product:openSUSE-dvd5-dvd-promo-x86_64',
        '_product:openSUSE-dvd9-dvd-biarch-i586_x86_64'
    ]

    # for 13.2 we take the build number of the FTP tree
    def current_version(self):
        for binary in self.binaries_of_product('openSUSE:%s' % self.project, '_product:openSUSE-ftp-ftp-i586_x86_64'):
            result = re.match(r'openSUSE.*Build(.*)-Media1.report', binary)
            if result:
                return result.group(1)

        raise Exception("can't find 13.2 version")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Commands to work with staging projects')
    parser.add_argument('project', metavar='P', type=str, default='Factory',
                        help='openSUSE version to make the check (Factory, 13.2)')
    parser.add_argument('-D', '--dryrun', dest="dryrun", action="store_true",  default=False,
                        help="dry run: do not actually publish")
    parser.add_argument('--restart-tests', dest="restart_tests", action="store_true", default=False,
                        help="Restart failed tests using external openqa helper script (must be setup with keys)")
    parser.add_argument("--osc-debug", action="store_true", help="osc debug output")

    args = parser.parse_args()

    totest_class = {
        'Factory': ToTestFactory,
        'Factory:PowerPC': ToTestFactoryPowerPC,
        'Factory:ARM': ToTestFactoryARM,
        '13.2': ToTest132,
    }

    if args.project not in totest_class:
        print 'Project %s not recognized. Possible values [%s]' % (args.project,
                                                                   ', '.join(totest_class))
        parser.print_help()
        exit(-1)

    osc.conf.get_config()
    Config('openSUSE:%s' % args.project)
    if (args.osc_debug):
        osc.conf.config['debug'] = True

    totest = totest_class[args.project](args.project, args.dryrun, args.restart_tests)
    totest.totest()
