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

from osclib.stagingapi import StagingAPI


# QA Results
QA_INPROGRESS = 1
QA_FAILED = 2
QA_PASSED = 3


class ToTestBase(object):
    """Base class to store the basic interface"""

    def __init__(self, project):
        self.project = project
        self.api = StagingAPI(osc.conf.config['apiurl'], opensuse=project)

    def openqa_version(self):
        return self.project

    def iso_prefix(self):
        return self.project

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
        for binary in self.binaries_of_product('openSUSE:%s:ToTest' % self.project, '_product:openSUSE-cd-mini-i586'):
            result = re.match(r'openSUSE-%s-NET-i586-Snapshot(.*)-Media.iso' % self.iso_prefix(),
                              binary)
            if result:
                return result.group(1)

        return None

    def find_openqa_results(self, snapshot):
        """Return the openqa jobs of a given snapshot and filter out the
        cloned jobs

        """

        url = 'https://openqa.opensuse.org/api/v1/' \
              'jobs?version={}&build={}&distri=opensuse'.format(self.openqa_version(), snapshot)
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

    def find_failed_module(self, result):
        # print json.dumps(result, sort_keys=True, indent=4)
        for module in result['testmodules']:
            if module['result'] != 'fail':
                continue
            flags = module['flags']
            if 'fatal' in flags or 'important' in flags:
                return module['name']
                break
            print module['name'], module['result'], module['flags']

    def overall_result(self, snapshot):
        """Analyze the openQA jobs of a given snapshot Returns a QAResult"""

        if snapshot == None:
            return QA_FAILED

        jobs = self.find_openqa_results(snapshot)

        if len(jobs) < 90:  # not yet scheduled
            print 'we have only %s jobs' % len(jobs)
            return QA_INPROGRESS

        number_of_fails = 0
        in_progress = False
        for job in jobs:
            # print json.dumps(job, sort_keys=True, indent=4)
            if job['result'] in ('failed', 'incomplete'):
                jobname = job['name'] + '@' + job['settings']['MACHINE']
                if jobname in self.known_failures:
                    self.known_failures.remove(jobname)
                    continue
                number_of_fails += 1
                # print json.dumps(job, sort_keys=True, indent=4), jobname
                url = 'https://openqa.opensuse.org/tests/%s' % job['id']
                result = json.load(self.api.retried_GET(url + '/file/results.json'))
                failedmodule = self.find_failed_module(result)
                print jobname, url, failedmodule, job['retry_avbl']
                # if number_of_fails < 3: continue
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

        for product in ['_product:openSUSE-ftp-ftp-i586_x86_64',
                        '_product:openSUSE-Addon-NonOss-ftp-ftp-i586_x86_64'] + self.main_products:
            if not self.package_ok('openSUSE:%s' % self.project, product, 'images', 'local'):
                return False

        if not self.all_repos_done('openSUSE:%s:Live' % self.project):
            return False

        for product in ['kiwi-image-livecd-kde.i586',
                        'kiwi-image-livecd-gnome.i586',
                        'kiwi-image-livecd-x11']:
            if not self.package_ok('openSUSE:%s:Live' % self.project, product, 'standard', 'i586'):
                return False

        for product in ['kiwi-image-livecd-kde.x86_64',
                        'kiwi-image-livecd-gnome.x86_64',
                        'kiwi-image-livecd-x11']:
            if not self.package_ok('openSUSE:%s:Live' % self.project, product, 'standard', 'x86_64'):
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

        self.release_package('openSUSE:%s' % self.project, '_product:openSUSE-ftp-ftp-i586_x86_64')
        self.release_package('openSUSE:%s' % self.project, '_product:openSUSE-Addon-NonOss-ftp-ftp-i586_x86_64')
        for cd in ['kiwi-image-livecd-kde.i586',
                   'kiwi-image-livecd-kde.x86_64',
                   'kiwi-image-livecd-gnome.i586',
                   'kiwi-image-livecd-gnome.x86_64',
                   'kiwi-image-livecd-x11']:
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

        if can_publish:
            self.publish_factory_totest()
            can_release = False  # we have to wait

        if can_release:
            self.update_totest(new_snapshot)


class ToTestFactory(ToTestBase):
    known_failures = [
        'opensuse-Tumbleweed-DVD-x86_64-Build-update_123@64bit',
        'opensuse-Tumbleweed-NET-x86_64-Build-update_121@64bit',
        'opensuse-Tumbleweed-NET-x86_64-Build-update_122@64bit',
        'opensuse-Tumbleweed-NET-x86_64-Build-update_123@64bit',
        'opensuse-Tumbleweed-NET-x86_64-Build-zdup-13.2-M0@64bit', # broken in 20140915
        'opensuse-Tumbleweed-NET-i586-Build-zdup-13.1-kde@32bit', # broken in 20140915
        'opensuse-Tumbleweed-NET-x86_64-Build-zdup-13.1-gnome@64bit', # broken in 20140915
        'opensuse-Tumbleweed-Rescue-CD-x86_64-Build-rescue@uefi-usb',
        'opensuse-Tumbleweed-KDE-Live-x86_64-Build-kde-live@uefi-usb',
        'opensuse-Tumbleweed-GNOME-Live-x86_64-Build-gnome-live@uefi-usb'
    ]
    
    main_products = ['_product:openSUSE-dvd5-dvd-i586',
                     '_product:openSUSE-dvd5-dvd-x86_64',
                     '_product:openSUSE-cd-mini-i586',
                     '_product:openSUSE-cd-mini-x86_64']

    def __init__(self, project):
        ToTestBase.__init__(self, project)

    def openqa_version(self):
        return 'Tumbleweed'

    def iso_prefix(self):
        return 'Tumbleweed'

    # for Factory we check the version of the release package
    def current_version(self):
        url = self.api.makeurl(['build', 'openSUSE:%s' % self.project, 'standard', 'x86_64',
                                '_product:openSUSE-release'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)
        raise Exception("can't find factory version")

class ToTest132(ToTestBase):
    known_failures = [
      'opensuse-13.2-DVD-Biarch-i586-x86_64-Build-update_123@32bit',
      'opensuse-13.2-DVD-Biarch-i586-x86_64-Build-update_122@32bit',
      'opensuse-13.2-DVD-Biarch-i586-x86_64-Build-update_121@32bit'
    ]

    main_products = ['_product:openSUSE-dvd5-dvd-i586',
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

    args = parser.parse_args()

    totest_class = {
        'Factory': ToTestFactory,
        '13.2': ToTest132,
    }

    if args.project not in totest_class:
        print 'Project %s not recognized. Possible values [%s]' % (args.project,
                                                                   ', '.join(totest_class))
        parser.print_help()
        exit(-1)

    osc.conf.get_config()
    #osc.conf.config['debug'] = True

    totest = totest_class[args.project](args.project)
    totest.totest()
