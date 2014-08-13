#!/usr/bin/python
# Copyright (c) 2014 SUSE Linux Products GmbH
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

import httplib
import re
from urlparse import urlparse
from pprint import pprint
import smtplib
from email.mime.text import MIMEText
import os
import sys

url = "http://download.opensuse.org/factory/iso/"
iso = "openSUSE-Factory-DVD-x86_64-Current.iso"
changes = "Changes.%s.txt "
current_fn = os.path.join(os.path.dirname(__file__), "announcer-current-version")

u = urlparse(url+iso)
conn = httplib.HTTPConnection(u.hostname, 80)
conn.request('HEAD', u.path)
res = conn.getresponse()
if res.status != 302:
    raise Exception("http fail: %s %s"%(res.status, res.reason))

loc = res.getheader('location')
if loc is None:
    raise Exception("empty location!")

m = re.search('Snapshot(\d+)-Media', loc)
if m is None:
    raise Exception("invalid location")

version = m.group(1)

if os.path.lexists(current_fn):
    prev = os.readlink(current_fn)
    if prev == version:
        sys.exit(0)

u = urlparse(url+changes%version)
conn = httplib.HTTPConnection(u.hostname, 80)
conn.request('HEAD', u.path)
res = conn.getresponse()
if res.status == 302:

    loc = res.getheader('location')
    if loc is None:
	raise Exception("empty location!")
    u = urlparse(loc)

conn = httplib.HTTPConnection(u.hostname, 80)
conn.request('GET', u.path)
res = conn.getresponse()
if res.status != 200:
    raise Exception("http fail: %s %s"%(res.status, res.reason))

msg = MIMEText(res.read())
msg['Subject'] = 'New Factory snapshot %s released!'%version
msg['From'] = "Ludwig Nussel <ludwig.nussel@suse.de>"
msg['To'] = "opensuse-factory@opensuse.org"

s = smtplib.SMTP('relay.suse.de')
s.sendmail('ludwig.nussel@suse.de', [msg['To']], msg.as_string())
s.quit()

tmpfn = os.path.join(os.path.dirname(__file__), ".announcer-current-version")
os.symlink(version, tmpfn)
os.rename(tmpfn, current_fn)

# vim: sw=4 et
