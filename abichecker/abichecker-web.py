#!/usr/bin/python
# Copyright (c) 2015 SUSE Linux Products GmbH
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

import os
import sys
from abichecker_dbmodel import *
from xdg.BaseDirectory import save_cache_path

CACHEDIR = save_cache_path('opensuse-abi-checker')

from flask import Flask, request, session, url_for, redirect, \
render_template, send_file, abort, g, flash, _app_ctx_stack

app = Flask(__name__)
app.config.from_object(__name__)

#app.cli.command('initdb')
#def initdb_command():
#    """Creates the database tables."""
#    Base.metadata.create_all(db_engine())

@app.route('/')
def list():
    session = db_session()
    reports = session.query(ABICheck).all()

    return render_template('index.html', reports = reports)

@app.route('/request/<int:request_id>')
def request(request_id):
    session = db_session()
    reports = session.query(ABICheck).filter(ABICheck.request_id == request_id)

    return render_template('index.html', reports = reports)


@app.route('/report/<int:report_id>')
def show(report_id):
    session = db_session()
    report = session.query(ABICheck).filter(ABICheck.id == report_id).one()

    return render_template('libreport.html', report = report)

@app.route('/report/<int:report_id>/<int:libreport_id>')
def libreport(report_id, libreport_id):
    session = db_session()

    report = session.query(LibReport).filter(LibReport.id == libreport_id).one()

    fn = os.path.join(CACHEDIR, report.htmlreport)

    return send_file(fn)


application = app
if __name__ == '__main__':
    Base.metadata.create_all(db_engine())
    application.run(debug=True)

# vim: sw=4 et
