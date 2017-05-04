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

from flask import Flask
from flask import request
from flask import make_response

import re
import os
import sys
from urlparse import urlparse

import re
digits_re = re.compile('^[0-9]+$')

BASE_DIR = '/var/lib'

app = Flask(__name__)

def get_dir(url):
    return os.path.join(BASE_DIR, urlparse(url).path.lstrip('/'))

@app.route('/')
def list():
    _dir = get_dir(request.url_root)
    fn = os.path.join(_dir, 'current')
    current = None
    if os.path.exists(fn):
        current = os.readlink(fn)

    ret = ''
    for i in sorted(os.listdir(_dir), reverse=True):
        if not digits_re.match(i):
            continue
        ret = ret + '<a href="diff/%s">%s</a>'%(i,i)
        if i == current:
            ret = ret + " &lt;--"
        ret = ret + '<br/>'
    return ret

@app.route('/current', methods=['GET', 'POST'])
def current():
    _dir = get_dir(request.url_root)
    fn = os.path.join(_dir, 'current')
    if request.method == 'POST':
        if not 'version' in request.form:
            return "missing version", 400
        version = request.form['version']
        if not digits_re.match(version):
            return "malformed version", 400
        if not os.path.exists(os.path.join(_dir, version)):
            return "invalid version", 400
        tmpfn = os.path.join(_dir,'.'+version)
        app.logger.debug(tmpfn)
        if os.path.exists(tmpfn):
            os.unlink(tmpfn)
        os.symlink(version, tmpfn)
        os.rename(tmpfn, fn)
        return "ok"
    else:
        if not os.path.exists(fn):
            return "", 404
        return os.readlink(fn)

@app.route('/diff/<int:version>')
def diff(version):
    _dir = get_dir(request.url_root)
    version = str(version)
    if not os.path.exists(os.path.join(_dir, version)):
        return "invalid version", 400
    import subprocess
    cmd = [os.path.dirname(os.path.abspath(__file__))+'/factory-package-news.py', \
            'diff', '--dir', _dir, "current", version]
    app.logger.debug(cmd)
    response = make_response(subprocess.check_output(cmd))
    response.content_type = "text/plain"
    return response

if __name__ == '__main__':
    from optparse import OptionParser
    parser = OptionParser()
    parser.add_option("--debug", action="store_true", help="debug output")
    parser.add_option("--host", metavar="IP", help="ip to listen to")
    (options, args) = parser.parse_args()
    app.run(debug=options.debug, host=options.host)

application = app

# vim: sw=4 et
