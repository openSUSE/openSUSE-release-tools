#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import os
import os.path
import sys
import json

from datetime import date

from osc import cmdln, oscerr

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)

from osclib.stagingapi import StagingAPI
from osclib.comments import CommentAPI

def tt_get_current_snapshot(self):
    """Return the current snapshot in Factory:ToTest"""
    
    # for now we hardcode all kind of things 
    url = self.api.makeurl(['build', 'openSUSE:Factory:ToTest', 'images', 'local', '_product:openSUSE-cd-mini-i586'])
    f = self.api.retried_GET(url)
    root = ET.parse(f).getroot()
    for binary in root.findall('binary'):
        result = re.match(r'openSUSE-Factory-NET-i586-Snapshot(.*)-Media.iso', binary.get('filename'))
        if result:
            return result.group(1)
    
    return None

def tt_find_openqa_results(self, snapshot):
    """ Return the openqa jobs of a given snapshot 
    and filter out the cloned jobs
    """

    url = "https://openqa.opensuse.org/api/v1/jobs?version=FTT&build={}&distri=opensuse".format(snapshot)
    f = self.api.retried_GET(url)
    jobs = []
    for job in json.load(f)['jobs']:
        if job['clone_id']: continue
        job['name'] = job['name'].replace(snapshot, '')
        jobs.append(job)
    return jobs

class QAResult: # no python 3.4
    InProgress = 1
    Failed = 2
    Passed = 3

def tt_result2str(result):
    if result == QAResult.InProgress:
        return 'inprogress'
    elif result == QAResult.Failed:
        return 'failed'
    else:
        return 'passed'

def tt_overall_result(self, snapshot):
    """ Analyze the openQA jobs of a given snapshot
    Returns a QAResult
    """

    jobs = self.tt_find_openqa_results(snapshot)

    known_failures = [
        'opensuse-FTT-DVD-x86_64-Build-doc@64bit',
        'opensuse-FTT-DVD-x86_64-Build-update_123@64bit',
        'opensuse-FTT-DVD-x86_64-Build-update_13.1-gnome@64bit',
        'opensuse-FTT-GNOME-Live-i686-Build-gnome-live@32bit',
        'opensuse-FTT-GNOME-Live-x86_64-Build-gnome-live@64bit',
        'opensuse-FTT-GNOME-Live-x86_64-Build-gnome-live@USBboot_64',
        'opensuse-FTT-KDE-Live-i686-Build-kde-live@32bit',
        'opensuse-FTT-KDE-Live-x86_64-Build-kde-live@64bit',
        'opensuse-FTT-KDE-Live-x86_64-Build-kde-live@USBboot_64',
        'opensuse-FTT-NET-x86_64-Build-update_121@64bit',
        'opensuse-FTT-NET-x86_64-Build-update_122@64bit',
        'opensuse-FTT-NET-x86_64-Build-update_123@64bit',
        'opensuse-FTT-Rescue-CD-i686-Build-rescue@32bit',
        'opensuse-FTT-Rescue-CD-x86_64-Build-rescue@64bit',
        'opensuse-FTT-NET-x86_64-Build-uefi@64bit',
        'opensuse-FTT-DVD-x86_64-Build-dual_windows8@64bit',
        'opensuse-FTT-NET-x86_64-Build-dual_windows8@64bit',
    ]

    if len(jobs) < 80: # not yet scheduled
        print "we have only", len(jobs), "jobs"
        return QAResult.InProgress

    number_of_fails = 0
    for job in jobs:
        #print json.dumps(job, sort_keys=True, indent=4)
        if job['result'] == 'failed' or job['result'] == 'incomplete' :
            jobname = job['name'] + "@" + job['settings']['MACHINE']
            if jobname in known_failures:
                known_failures.remove(jobname)
                continue
            number_of_fails += 1
            #print json.dumps(job, sort_keys=True, indent=4), jobname
            print jobname, "https://openqa.opensuse.org/tests/{}".format(job['id'])
            if number_of_fails < 3: continue
            return QAResult.Failed
        elif job['result'] == 'passed':
            continue
        elif job['result'] == 'none':
            return QAResult.InProgress
        else:
            raise Exception(job['result'])
            
    if known_failures:
        print "Some are now passing", known_failures
    return QAResult.Passed

def tt_all_repos_done(self, project, codes=['published', 'unpublished']):
    """
    Check the build result of the project and only return True if all 
    repos of that project are either published or unpublished
    """
    url = self.api.makeurl(['build', project, '_result'], {'code': 'failed' })
    f = self.api.retried_GET(url)
    root = ET.parse(f).getroot()
    for repo in root.findall('result'):
        if repo.get('dirty', '') == 'true':
            print repo.get('project'), repo.get('repository'), repo.get('arch'), 'dirty'
            return False
        if repo.get('code') not in codes:
            print repo.get('project'), repo.get('repository'), repo.get('arch'), repo.get('code')
            return False
    return True

def tt_maxsize_for_package(self, package):
    if re.match(r'.*-mini-.*', package ):
        return 737280000 # a CD needs to match

    if re.match(r'.*-dvd5-.*', package ):
        return 4700372992 # a DVD needs to match

    if re.match(r'.*-image-livecd-x11.*', package ):
        return 681574400 # not a full CD

    if re.match(r'.*-image-livecd.*', package ):
        return 999999999  # a GB stick

    if package == '_product:openSUSE-ftp-ftp-i586_x86_64':
        return None
    
    raise Exception('No maxsize for {}'.format(package))

def tt_package_ok(self, project, package, repository, arch):
    """
    Checks one package in a project and returns True if it's succeeded
    """
    query = {'package': package, 'repository': repository, 'arch': arch }

    url = self.api.makeurl(['build', project, '_result'], query)
    f = self.api.retried_GET(url)
    root = ET.parse(f).getroot()
    for repo in root.findall('result'):
        status = repo.find('status')
        if status.get('code') != 'succeeded':
            print project, package, repository, arch, status.get('code')
            return False

    maxsize = self.tt_maxsize_for_package(package)
    if not maxsize:
        return True

    url = self.api.makeurl(['build', project, repository, arch, package])
    f = self.api.retried_GET(url)
    root = ET.parse(f).getroot()
    for binary in root.findall('binary'):
        if not binary.get('filename', '').endswith('.iso'):
            continue
        isosize=int(binary.get('size', 0))
        if isosize > maxsize:
            print project, package, repository, arch, 'too large by {} bytes'.format(isosize-maxsize)
            return False

    return True
    
def tt_factory_snapshottable(self):
    """
    Check various conditions required for factory to be snapshotable
    """

    if not self.tt_all_repos_done('openSUSE:Factory'):
        return False

    for product in ['_product:openSUSE-ftp-ftp-i586_x86_64', 
                    '_product:openSUSE-dvd5-dvd-i586',
                    '_product:openSUSE-dvd5-dvd-x86_64',
                    '_product:openSUSE-cd-mini-i586',
                    '_product:openSUSE-cd-mini-x86_64']:
        if not self.tt_package_ok('openSUSE:Factory', product, 'images', 'local'):
            return False

    if not self.tt_all_repos_done('openSUSE:Factory:Live'):
        return False

    for product in ['kiwi-image-livecd-kde.i586',
                    'kiwi-image-livecd-gnome.i586',
                    'kiwi-image-livecd-x11']:
        if not self.tt_package_ok('openSUSE:Factory:Live', product, 'standard', 'i586'):
            return False

    for product in ['kiwi-image-livecd-kde.x86_64',
                    'kiwi-image-livecd-gnome.x86_64',
                    'kiwi-image-livecd-x11']:
        if not self.tt_package_ok('openSUSE:Factory:Live', product, 'standard', 'x86_64'):
            return False

    return True

def tt_release_package(self, project, package, set_release=None):
    query = { 'cmd': 'release' }
    
    if set_release:
        query["setrelease"] = set_release
    
    baseurl = ['source', project, package]

    url = self.api.makeurl(baseurl, query=query)
    self.api.retried_POST(url)
    
def tt_update_totest(self, snapshot):
    print "Updating snapshot {}".format(snapshot)
    self.api.switch_flag_in_prj('openSUSE:Factory:ToTest', flag='publish', state='disable')

    self.tt_release_package('openSUSE:Factory', '_product:openSUSE-ftp-ftp-i586_x86_64')
    for cd in ['kiwi-image-livecd-kde.i586',
               'kiwi-image-livecd-kde.x86_64',
               'kiwi-image-livecd-gnome.i586',
               'kiwi-image-livecd-gnome.x86_64',
               'kiwi-image-livecd-x11']:
        self.tt_release_package('openSUSE:Factory:Live', cd, set_release='Snapshot{}'.format(snapshot))
        
    for cd in ['_product:openSUSE-dvd5-dvd-i586',
               '_product:openSUSE-dvd5-dvd-x86_64',
               '_product:openSUSE-cd-mini-i586',
               '_product:openSUSE-cd-mini-x86_64']:
        self.tt_release_package('openSUSE:Factory', cd, set_release='Snapshot{}'.format(snapshot))

def tt_publish_factory_totest(self):
    print "Publish ToTest"
    self.api.switch_flag_in_prj('openSUSE:Factory:ToTest', flag='publish', state='enable')

def tt_totest_is_publishing(self):
    """Find out if the publishing flag is set in totest's _meta"""
    
    url = self.api.makeurl(['source', 'openSUSE:Factory:ToTest', '_meta'])
    f = self.api.retried_GET(url)
    root = ET.parse(f).getroot()
    if not root.find('publish'): # default true
        return True

    for flag in root.find('publish'):
        if flag.get('repository', None) or flag.get('arch', None):
            continue
        if flag.tag == 'enable':
            return True

    return False

def tt_build_of_ftp_tree(self, project):
    """Determine the build id of the FTP tree product in the given project"""
    
    url = self.api.makeurl(['build', project, 'images', 'local', '_product:openSUSE-ftp-ftp-i586_x86_64'])
    f = self.api.retried_GET(url)
    root = ET.parse(f).getroot()
    for binary in root.findall('binary'):
        binary = binary.get('filename', '')
        result = re.match(r'.*Build(.*)-Media1.report', binary)
        if result:
            return result.group(1)
    print ET.tostring(root)
    raise Exception('No media1.report found')

def do_totest(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    Usage:
        osc totest 
    """

    # verify the argument counts match the commands
    if len(args) != 0:
        raise oscerr.WrongArgs("we don't need arguments")
   
    # init the obs access
    opts.apiurl = self.get_api_url()
    opts.verbose = False
    self.api = StagingAPI(opts.apiurl)

    current_snapshot = self.tt_get_current_snapshot()
    new_snapshot = date.today().strftime("%Y%m%d")

    current_result = self.tt_overall_result(current_snapshot)
    print "current_snapshot", current_snapshot, tt_result2str(current_result)
    if current_result == QAResult.Failed:
        sys.exit(1)
    can_release = current_result != QAResult.InProgress and self.tt_factory_snapshottable()
    
    # not overwriting
    if new_snapshot == current_snapshot:
        can_release = False
    elif self.tt_build_of_ftp_tree('openSUSE:Factory') == self.tt_build_of_ftp_tree('openSUSE:Factory:ToTest'):
        # there was no change in factory since the last release, so drop it
        print "FTP tree is the same"
        can_release = False
    elif not self.tt_all_repos_done('openSUSE:Factory:ToTest'):
        # the repos have to be done, otherwise we better not touch them with a new release
        can_release = False

    can_publish = current_result == QAResult.Passed

    # already published
    if self.tt_totest_is_publishing():
        can_publish = False

    if can_publish:
        self.tt_publish_factory_totest()
        can_release = False # we have to wait

    if can_release:
        self.tt_update_totest(new_snapshot)
