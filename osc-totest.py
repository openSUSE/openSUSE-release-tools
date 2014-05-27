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

from osc import cmdln, oscerr

# Expand sys.path to search modules inside the pluging directory
_plugin_dir = os.path.expanduser('~/.osc-plugins')
sys.path.append(_plugin_dir)

from osclib.stagingapi import StagingAPI
from osclib.comments import CommentAPI

def get_current_snapshot(self):
    """Return the current snapshot in Factory:ToTest"""
    
    # for now we hardcode all kind of things 
    url = makeurl(self.api.apiurl, ['build', 'openSUSE:Factory:ToTest', 'images', 'local', '_product:openSUSE-cd-mini-i586'])
    f = http_GET(url)
    root = ET.parse(f).getroot()
    for binary in root.findall('binary'):
        result = re.match(r'openSUSE-Factory-NET-i586-Snapshot(.*)-Media.iso', binary.get('filename'))
        if result:
            return result.group(1)
    
    return None

def find_openqa_results(self, snapshot):
    """ Return the openqa jobs of a given snapshot 
    and filter out the cloned jobs
    """

    url = "https://openqa.opensuse.org/api/v1/jobs?version=FTT&build={}&distro=openSUSE".format(snapshot)
    f = http_GET(url)
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

def overall_result(self, snapshot):
    """ Analyze the openQA jobs of a given snapshot
    Returns a QAResult
    """

    jobs = self.find_openqa_results(snapshot)

    known_failures = [
        'opensuse-FTT-DVD-i586-Build-dual_windows8@32bit',
        'opensuse-FTT-DVD-i586-Build-minimalx+btrfs+nosephome@32bit',
        'opensuse-FTT-DVD-i586-Build-textmode@32bit',
        'opensuse-FTT-DVD-i586-Build-update_123@32bit',
        'opensuse-FTT-DVD-i586-Build-update_13.1-kde@32bit',
        'opensuse-FTT-DVD-x86_64-Build-doc@64bit',
        'opensuse-FTT-DVD-x86_64-Build-dual_windows8@64bit',
        'opensuse-FTT-DVD-x86_64-Build-minimalx+btrfs+nosephome@64bit',
        'opensuse-FTT-DVD-x86_64-Build-textmode@64bit',
        'opensuse-FTT-DVD-x86_64-Build-update_123@64bit',
        'opensuse-FTT-DVD-x86_64-Build-update_13.1-gnome@64bit',
        'opensuse-FTT-GNOME-Live-i686-Build-gnome-live@32bit',
        'opensuse-FTT-GNOME-Live-x86_64-Build-gnome-live@64bit',
        'opensuse-FTT-GNOME-Live-x86_64-Build-gnome-live@USBboot_64',
        'opensuse-FTT-KDE-Live-i686-Build-kde-live@32bit',
        'opensuse-FTT-KDE-Live-x86_64-Build-kde-live@64bit',
        'opensuse-FTT-KDE-Live-x86_64-Build-kde-live@USBboot_64',
        'opensuse-FTT-NET-i586-Build-dual_windows8@32bit',
        'opensuse-FTT-NET-i586-Build-textmode@32bit',
        'opensuse-FTT-NET-i586-Build-update_121@32bit',
        'opensuse-FTT-NET-i586-Build-update_122@32bit',
        'opensuse-FTT-NET-i586-Build-update_123@32bit',
        'opensuse-FTT-NET-x86_64-Build-dual_windows8@64bit',
        'opensuse-FTT-NET-x86_64-Build-textmode@64bit',
        'opensuse-FTT-NET-x86_64-Build-update_121@64bit',
        'opensuse-FTT-NET-x86_64-Build-update_122@64bit',
        'opensuse-FTT-NET-x86_64-Build-update_123@64bit',
        'opensuse-FTT-Rescue-CD-i686-Build-rescue@32bit',
        'opensuse-FTT-Rescue-CD-x86_64-Build-rescue@64bit'
    ]

    for job in jobs:
        #print json.dumps(job, sort_keys=True, indent=4)
        if job['result'] == 'failed':
            jobname = job['name'] + "@" + job['machine']
            if jobname in known_failures:
                known_failures.remove(jobname)
                continue
            print json.dumps(job, sort_keys=True, indent=4), known_failures
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

    snapshot = self.get_current_snapshot()
    print self.overall_result(snapshot)
