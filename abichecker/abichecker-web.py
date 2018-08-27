#!/usr/bin/python

import os
import sys
from abichecker_dbmodel import *
from abichecker_common import CACHEDIR
from abichecker_common import Config

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
    requests = session.query(Request).order_by(Request.id.desc()).limit(200).all()

    return render_template('index.html', requests = requests)

@app.route('/request/<int:request_id>')
def request(request_id):
    session = db_session()
    request = session.query(Request).filter(Request.id == request_id).one()
    config = Config(session)

    return render_template('request.html', request = request, obsurl = config.get('obs-weburl', "https://build.opensuse.org/"))


@app.route('/report/<int:report_id>')
def report(report_id):
    session = db_session()

    report = session.query(LibReport).filter(LibReport.id == report_id).one()

    fn = os.path.join(CACHEDIR, report.htmlreport)

    return send_file(fn)


application = app
if __name__ == '__main__':
    Base.metadata.create_all(db_engine())
    application.run(debug=True)

