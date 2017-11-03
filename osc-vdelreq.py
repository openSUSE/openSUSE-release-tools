#!/usr/bin/python
# Copyright (c) 2017 SUSE LLC
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

from __future__ import print_function

import os
import os.path
import sys

from xml.etree import cElementTree as ET

import osc.core
import osc.conf

from osc import cmdln
from osc import oscerr

def _has_binary(self, project, package):
    query = {'view': 'binarylist', 'package': package, 'multibuild': '1'}
    pkg_binarylist = ET.parse(osc.core.http_GET(osc.core.makeurl(self.apiurl, ['build', project, '_result'], query=query))).getroot()
    for binary in pkg_binarylist.findall('./result/binarylist/binary'):
        return 'Yes'
    return 'No'


def list_virtually_accepted_request(self, project, opts):
    state_cond = "state/@name='review'+or+state/@name='revoked'+or+state/@name='revoked'"
    if opts.all:
        state_cond += "+or+state/@name='accepted'"
    query = "match=({})+and+(action/target/@project='{}'+and+action/@type='delete')+and+"\
            "((review/@state='new'+or+review/@state='accepted')+and+review/@by_group='{}')".format(state_cond, project, opts.delreq_review)
    url = osc.core.makeurl(self.apiurl, ['search', 'request'], query)
    f = osc.core.http_GET(url)
    root = ET.parse(f).getroot()
    rqs = []
    for rq in root.findall('request'):
        has_binary = 'No'
        id = rq.attrib['id']
        rq_state = rq.find('state').get('name')
        pkg = rq.find('action/target').get('package')
        if rq_state != 'accepted':
            has_binary = self._has_binary(project, pkg)
        for review in rq.findall('review'):
            if review.get('by_group') and review.attrib['by_group'] == opts.delreq_review:
                delreq_review_state = review.attrib['state']

        content = {"id": int(id), "package": pkg, "rq_state": rq_state, "delreq_review_state": delreq_review_state, "has_binary": has_binary}
        rqs.append(content)

    rqs.sort(key=lambda d: d['id'])
    for rq in rqs:
        print("{} {} state is {} \n {} Virtually accept review is {} ( binary: {} )".format(str(rq['id']),
            rq['package'], rq['rq_state'], "-".rjust(len(str(rq['id']))+1, ' '), rq['delreq_review_state'], rq['has_binary']))

@cmdln.option('--delreq-review', dest='delreq_review', metavar='DELREQREVIEW', default='factory-maintainers',
        help='the additional reviews')
@cmdln.option('--all', action='store_true', default=False, help='shows all requests including accepted request')
def do_vdelreq(self, subcmd, opts, *args):
    """${cmd_name}: display pending virtual accept delete request

    osc vdelreq [OPT] COMMAND PROJECT
        Shows pending the virtual accept delete requests and the current state.

    ${cmd_option_list}

    "list" will list virtually accepted delete request.

    Usage:
        osc vdelreq [--delreq-review DELREQREVIEW] list PROJECT
    """

    self.apiurl = self.get_api_url()

    if len(args) == 0:
        raise oscerr.WrongArgs('No command given, see "osc help vdelreq"!')
    if len(args) < 2:
        raise oscerr.WrongArgs('No project given, see "osc help vdelreq"!')

    cmd = args[0]
    if cmd in ('list'):
        self.list_virtually_accepted_request(args[1], opts)
    else:
        raise oscerr.WrongArgs('Unknown command: %s' % cmd)

# vim: sw=4 et
