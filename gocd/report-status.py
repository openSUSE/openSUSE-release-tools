#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2018 SUSE Linux GmbH
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import os
import sys
from xml.etree import cElementTree as ET

import osc.core
from osclib.core import target_archs

try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_POST = osc.core.http_POST

def report_pipeline(args, architecture, is_last):
    url = makeurl(args.apiurl, [
                  'build', args.project, args.repository, architecture], {'view': 'status'})
    root = ET.parse(http_GET(url)).getroot()
    buildid = root.find('buildid')
    if buildid is None:
        return False
    buildid = buildid.text
    url = makeurl(args.apiurl, ['status_reports', 'built', args.project,
                                args.repository, architecture, 'reports', buildid])
    name = 'gitlab-pipeline'
    state = args.state
    # this is a little bit ugly, but we don't need 2 failures. So save a success for the
    # other archs to mark them as visited - pending we put in both
    if not is_last:
        if state == 'failure':
            state = 'success'
        name = name + ':' + architecture
    report_url = os.environ.get('GO_SERVER_URL').replace(':8154', '')
    report_url = report_url + '/tab/build/detail/{}/{}/{}/{}/{}#tab-console'.format(os.environ.get('GO_PIPELINE_NAME'), os.environ.get('GO_PIPELINE_COUNTER'), os.environ.get('GO_STAGE_NAME'), os.environ.get('GO_STAGE_COUNTER'), os.environ.get('GO_JOB_NAME'))
    xml = check_xml(report_url, state, name)
    try:
        http_POST(url, data=xml)
    except HTTPError:
        print('failed to post status to ' + url)
        sys.exit(1)

def check_xml(url, state, name):
    check = ET.Element('check')
    if url:
        se = ET.SubElement(check, 'url')
        se.text = url
    se = ET.SubElement(check, 'state')
    se.text = state
    se = ET.SubElement(check, 'name')
    se.text = name
    return ET.tostring(check)

if __name__ == '__main__':
    description = 'Create SR from FactoryCandidates to '\
                  'openSUSE Leap project for new build succeded packages.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL', required=True)
    parser.add_argument('-p', '--project', metavar='PROJECT', help='Project', required=True)
    parser.add_argument('-r', '--repository', metavar='REPOSITORY', help='Repository', required=True)
    parser.add_argument('-s', '--state', metavar='STATE', help='Status to report', required=True)

    args = parser.parse_args()
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    #osc.conf.config['debug'] = 1

    architectures = sorted(target_archs(args.apiurl, args.project, args.repository))
    for arch in architectures:
        report_pipeline(args, arch, arch == architectures[-1])
