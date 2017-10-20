#!/usr/bin/python
# Copyright (c) 2015,2016 SUSE LLC
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

import json
import time
import osc
import osc.core
import osc.conf
import xml.etree.ElementTree as ET
import smtplib
from email import Charset
from email.mime.text import MIMEText
import email.utils
import logging
import argparse
import sys
from collections import namedtuple

#http://wordeology.com/computer/how-to-send-good-unicode-email-with-python.html
Charset.add_charset('utf-8', Charset.QP, Charset.QP, 'utf-8')

# FIXME: compute from apiurl
URL="https://build.opensuse.org/project/status?&project=%s&ignore_pending=true&limit_to_fails=true&include_versions=false&format=json"
# for maintainer search
FACTORY='openSUSE:Factory'

class RemindedPackage(object):
    def __init__(self, firstfail, reminded, remindCount, bug):
        self.firstfail=firstfail
        self.reminded=reminded
        self.bug=bug
        self.remindCount=remindCount

    def __str__(self):
        return '{} {} {} {}'.format(self.firstfail, self.reminded, self.bug, self.remindCount)

def jdefault(o):
        return o.__dict__

MAIL_TEMPLATES = ( u"""Dear %(recepient)s,

Please be informed that '%(package)s' in %(project)s has
not had a successful build since %(date)s. See
https://build.opensuse.org/package/show/%(project)s/%(package)s

This can be due to an error in your package directly or could be
caused by a package you depend on to build. In any case, please do
your utmost to get the status back to building.

You will get another reminder in a week if the package still fails
by then.

*** NOTE:
This is an attempt to raise awareness of the maintainers about
broken builds in %(project)s. You receive this mail because you are
marked as maintainer for the above mentioned package (or project
maintainer if the package has no explicit maintainer assigned)

Kind regards,
%(sender)s
""",
u"""Dear %(recepient)s,

Despite the reminder of one week ago, we have to inform you that
'%(package)s' is still failing in %(project)s. See
https://build.opensuse.org/package/show/%(project)s/%(package)s

It has been failing to build since %(date)s.

Please find the time to fix the build of this package. If needed,
also reach out to the broader community, trying to find somebody to
help you fix this package.

*** NOTE:
This is an attempt to raise awareness of the maintainers about
broken builds in Tumbleweed. You receive this mail because you are
marked as maintainer for the above mentioned package (or project
maintainer if the package has no explicit maintainer assigned)

Kind regards,
%(sender)s
"""
)

def main(args):

    # do some work here
    logger = logging.getLogger("build-fail-reminder")
    logger.info("start")

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.osc_debug
    apiurl = osc.conf.config['apiurl']

    sender = args.sender
    project = args.project

    logger.debug('loading build fails for %s'%project)
    json_data = osc.core.http_GET(URL%project)
    data = json.load(json_data)
    json_data.close()

    try:
        with open('{}.reminded.json'.format(project)) as json_data:
            RemindedLoaded = json.load(json_data)
        json_data.close()
    except:
        RemindedLoaded = {}
        pass

    seconds_to_remember = 7 * 86400
    now = int(time.time())

    Reminded = {}
    Person = {}

    # Go through all the failed packages and update the reminder
    for package in data:
        # Only consider packages that failed for > seconds_to_remember days (7 days)
        if package["firstfail"] < now - seconds_to_remember:
            if not package["name"] in RemindedLoaded.keys():
                # This is the first time we see this package failing for > 7 days
                reminded = now
                bug=""
                remindCount = 1
            else:
                if RemindedLoaded[package["name"]]["reminded"] < now - seconds_to_remember:
                    # We had seen this package in the last run - special treatment
                    reminded = now
                    bug="boo#123"
                    remindCount = RemindedLoaded[package["name"]]["remindCount"] + 1
                else:
                    reminded = RemindedLoaded[package["name"]]["reminded"]
                    remindCount = RemindedLoaded[package["name"]]["remindCount"]
                    bug = RemindedLoaded[package["name"]]["bug"]
            Reminded[package["name"]] = RemindedPackage(package["firstfail"], reminded, remindCount, bug)

    if not args.dry:
        with open('%s.reminded.json'%project, 'w') as json_result:
            json.dump(Reminded, json_result, default=jdefault)

    for package in Reminded:
        # Now we check on all the packages if we have to perform any reminder actions...
        if Reminded[package].reminded == now:
            # find the maintainers, try to not hammer the server too much
            query = {
                'binary': package,
                'project': FACTORY,
            }
            url = osc.core.makeurl(apiurl, ('search', 'owner'), query=query)
            root = ET.parse(osc.core.http_GET(url)).getroot()
            maintainers = set([p.get('name') for p in root.findall('.//person') if p.get('role') in ('maintainer', 'bugowner')])
            # TODO: expand groups if no persons found
            for userid in maintainers:
                if not userid in Person:
                    Person[userid] = osc.core.get_user_data(apiurl, userid, 'login', 'realname', 'email')
            if Reminded[package].remindCount in (1, 2):
                for userid in maintainers:
                    to = Person[userid][1]
                    fullname = Person[userid][2]
                    text = MAIL_TEMPLATES[Reminded[package].remindCount-1] % {
                                'recepient': to,
                                'sender': sender,
                                'project' : project,
                                'package' : package,
                                'date': time.ctime(Reminded[package].firstfail),
                                }
                    msg = MIMEText(text, _charset='UTF-8')
                    msg['Subject'] = '%s - %s - Build fail notification' % (project, package)
                    msg['To'] = email.utils.formataddr((to, fullname))
                    msg['From'] = sender
                    msg['Date'] = email.utils.formatdate()
                    msg['Message-ID'] = email.utils.make_msgid()
                    msg.add_header('Precedence', 'bulk')
                    msg.add_header('X-Mailer', '%s - Failure Notification' % project)
                    logger.info("%s: %s", msg['To'], msg['Subject'])
                    if args.dry:
                        logger.debug(msg.as_string())
                    else:
                        try:
                            s = smtplib.SMTP(args.relay)
                            s.sendmail(msg['From'], {msg['To'], sender }, msg.as_string())
                            s.quit()
                        except:
                            logger.error("Failed to send an email to %s (%s)" % (fullname, to))
                            pass

            elif Reminded[package].remindCount == 3:
                logger.warn( "Package '%s' has been failing for three weeks - let's create a bug report" % package)
            else:
                logger.warn( "Package '%s' is no longer maintained - send a mail to factory maintainers..." % package)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='boilerplate python commmand line program')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument("--dry", action="store_true", help="dry run")
    parser.add_argument("--debug", action="store_true", help="debug output")
    parser.add_argument("--verbose", action="store_true", help="verbose")
    parser.add_argument("--sender", metavar="SENDER", help="who the mail comes from", required=True)
    parser.add_argument("--project", metavar="PROJECT", help="which project to check", default="openSUSE:Factory")
    parser.add_argument("--relay", metavar="RELAY", help="relay server", required=True)
    parser.add_argument("--osc-debug", action="store_true", help="osc debug output")

    args = parser.parse_args()

    if args.debug:
        level  = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    else:
        level = None

    logging.basicConfig(level = level)

    sys.exit(main(args))

# vim: sw=4 et
