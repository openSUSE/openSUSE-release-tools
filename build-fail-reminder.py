#!/usr/bin/python3

import json
import time
import osc
import osc.core
import osc.conf
from lxml import etree as ET
import logging
import argparse
import sys
import yaml
import dateutil.parser
from urllib.error import HTTPError
from osclib.util import mail_send_with_details
import email.utils

# for maintainer search
FACTORY = 'openSUSE:Factory'
SEVEN_DAYS = 7 * 86400

apiurl = None
project = None


class RemindedPackage(object):
    def __init__(self, firstfail, problem, reminded, remindCount):
        self.firstfail = firstfail
        self.reminded = reminded
        self.remindCount = remindCount
        self.problem = problem

    def __str__(self):
        return '{} {} {} {}'.format(self.firstfail, self.reminded, self.remindCount, self.problem)


def jdefault(o):
    return o.__dict__


MAIL_TEMPLATES = (u"""Dear %(recipient)s,

Please be informed that '%(package)s' in %(project)s has
a problem since %(date)s:
  %(problem)s

See https://build.opensuse.org/package/show/%(project)s/%(package)s

This can be due to an error in your package directly or could be
caused by a package you depend on to build. In any case, please do
your utmost to get the status back to building.

You will get another reminder in a week if the package still shows
problems by then.

*** NOTE:
This is an attempt to raise awareness of the maintainers about
problems in %(project)s. You receive this mail because you are
marked as maintainer for the above mentioned package (or project
maintainer if the package has no explicit maintainer assigned)

Kind regards,
%(sender)s
""",
                  u"""Dear %(recipient)s,

Following-up the reminder of one week ago, we have to inform you that
'%(package)s' is still showing a problem in %(project)s. See
https://build.opensuse.org/package/show/%(project)s/%(package)s

Since %(date)s we noticed the following problem:
  %(problem)s

Please find the time to fix the build of this package. If needed,
also reach out to the broader community, trying to find somebody to
help you fix this package.

*** NOTE:
This is an attempt to raise awareness of the maintainers about
problems in %(project)s. You receive this mail because you are
marked as maintainer for the above mentioned package (or project
maintainer if the package has no explicit maintainer assigned)

Kind regards,
%(sender)s
""")


def SendMail(logger, project, sender, to, fullname, subject, text):
    try:
        xmailer = '{} - Problem Notification'.format(project)
        to = email.utils.formataddr((fullname, to))
        mail_send_with_details(sender=sender, to=to,
                               subject=subject, text=text, xmailer=xmailer,
                               relay=args.relay, dry=args.dry)
    except Exception as e:
        print(e)
        logger.error("Failed to send an email to %s (%s)" % (fullname, to))


def check_reminder(pname, first, problem, now, Reminded, RemindedLoaded):
    # Only consider packages that failed for > seconds_to_remember days (7 days)
    if first >= now - SEVEN_DAYS:
        return
    if pname not in RemindedLoaded:
        # This is the first time we see this package failing for > 7 days
        reminded = now
        remindCount = 1
    else:
        if RemindedLoaded[pname]["reminded"] < now - SEVEN_DAYS:
            # We had seen this package in the last run - special treatment
            reminded = now
            remindCount = RemindedLoaded[pname]["remindCount"] + 1
        else:
            reminded = RemindedLoaded[pname]["reminded"]
            remindCount = RemindedLoaded[pname]["remindCount"]
    Reminded[pname] = RemindedPackage(first, problem, reminded, remindCount)


def extract_package_name(source):
    _, _, _, rpm = source.split('/')
    # strip multibuild flavor
    package = rpm.split(':')[0]
    # check multi spec origin
    url = osc.core.makeurl(apiurl, ['source', project, package])
    root = ET.parse(osc.core.http_GET(url))
    for li in root.findall('linkinfo'):
        return li.get('package')
    return package


def main(args):

    # do some work here
    logger = logging.getLogger("build-fail-reminder")
    logger.info("start")

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.osc_debug
    global apiurl
    apiurl = osc.conf.config['apiurl']

    sender = args.sender
    global project
    project = args.project

    logger.debug('loading build fails for %s' % project)
    url = osc.core.makeurl(apiurl, ['source', f'{project}:Staging', 'dashboard', f'rebuildpacs.{project}-standard.yaml'])
    try:
        _data = osc.core.http_GET(url)
        rebuilddata = yaml.safe_load(_data)
        _data.close()
    except HTTPError as e:
        if e.code == 404:
            rebuilddata = {}
        else:
            raise e

    rebuilddata.setdefault('check', {})
    rebuilddata.setdefault('failed', {})
    rebuilddata.setdefault('unresolvable', {})

    reminded_json = args.json
    if not reminded_json:
        reminded_json = '{}.reminded.json'.format(project)

    try:
        with open(reminded_json) as json_data:
            RemindedLoaded = json.load(json_data)
    except FileNotFoundError:
        RemindedLoaded = {}

    now = int(time.time())

    Reminded = {}
    Person = {}
    ProjectComplainList = []

    # Go through all the failed packages and update the reminder
    for source, timestamp in rebuilddata['failed'].items():
        date = int(dateutil.parser.parse(timestamp).timestamp())
        check_reminder(extract_package_name(source), date, "Fails to build", now, Reminded, RemindedLoaded)

    for source, timestamp in rebuilddata['unresolvable'].items():
        date = int(dateutil.parser.parse(timestamp).timestamp())
        check_reminder(extract_package_name(source), date, "Unresolvable", now, Reminded, RemindedLoaded)

    repochecks = dict()
    for prpa, details in rebuilddata['check'].items():
        package = extract_package_name(prpa)
        date = int(dateutil.parser.parse(details["rebuild"]).timestamp())
        repochecks.setdefault(package, {"problems": set(), "rebuild": date})
        for problem in details["problem"]:
            repochecks[package]["problems"].add(problem)

        if repochecks[package]["rebuild"] > date:
            # prefer the youngest date
            repochecks[package]["rebuild"] = date

    for pname in repochecks:
        first_problem = sorted(repochecks[pname]["problems"])[0]
        check_reminder(pname, repochecks[pname]["rebuild"], f"Uninstallable: {first_problem}", now, Reminded, RemindedLoaded)

    if not args.dry:
        with open(reminded_json, 'w') as json_result:
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
            maintainers = set([p.get('name') for p in root.findall('.//person')
                              if p.get('role') in ('maintainer', 'bugowner')])
            # TODO: expand groups if no persons found
            for userid in maintainers:
                if userid not in Person:
                    Person[userid] = osc.core.get_user_data(apiurl, userid, 'login', 'realname', 'email')
            if Reminded[package].remindCount in (1, 2):
                for userid in maintainers:
                    to = Person[userid][2]
                    fullname = Person[userid][1]
                    subject = '%s - %s - Build problem notification' % (project, package)
                    text = MAIL_TEMPLATES[Reminded[package].remindCount - 1] % {
                        'recipient': fullname,
                        'sender': sender,
                        'project': project,
                        'package': package,
                        'problem': Reminded[package].problem,
                        'date': time.ctime(Reminded[package].firstfail)
                    }
                    SendMail(logger, project, sender, to, fullname, subject, text)
            elif Reminded[package].remindCount == 4:
                # Package has failed for 4 weeks - Collect packages to send a mail to openSUSE-factory@ (one mail per day max)
                ProjectComplainList.append(package)
            elif Reminded[package].remindCount == 6:
                # Package failed to build for 6 weeks - file a delete request
                r = osc.core.Request()
                r.add_action('delete', tgt_project=project, tgt_package=package)
                r.description = "[botdel] Package has had build problems for &gt;= 6 weeks"
                r.create(apiurl)

    if len(ProjectComplainList):
        # At least to report to the project for not building - send a mail to openSUSE-Factory
        ProjectComplainList.sort()
        to = 'openSUSE-Factory@opensuse.org'
        fullname = "openSUSE Factory - Mailing List"
        subject = "%(project)s - Build fail notification" % {'project': project}

        text = u"""Dear Package maintainers and hackers.

Below package(s) in %(project)s have had problems for at
least 4 weeks. We tried to send out notifications to the
configured bugowner/maintainers of the package(s), but so far no
fix has been submitted. This probably means that the
maintainer/bugowner did not yet find the time to look into the
matter and he/she would certainly appreciate help to get this
sorted.

""" % {'project': project}
        for pkg in ProjectComplainList:
            text += "- %s: %s\n" % (pkg, Reminded[pkg].problem)
        text += u"""
Unless somebody is stepping up and submitting fixes, the listed
package(s) are going to be removed from %(project)s.

Kind regards,
%(sender)s
""" % {'project': project, 'sender': sender}
        SendMail(logger, project, sender, to, fullname, subject, text)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Send e-mails about packages failing to build for a long time')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument("--dry", action="store_true", help="dry run")
    parser.add_argument("--debug", action="store_true", help="debug output")
    parser.add_argument("--verbose", action="store_true", help="verbose")
    parser.add_argument("--sender", metavar="SENDER", help="who the mail comes from", required=True)
    parser.add_argument("--project", metavar="PROJECT", help="which project to check", default="openSUSE:Factory")
    parser.add_argument("--relay", metavar="RELAY", help="relay server", required=True)
    parser.add_argument("--osc-debug", action="store_true", help="osc debug output")
    parser.add_argument("--json", metavar="JSON", help="filename to store reminds")

    args = parser.parse_args()

    if args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    else:
        level = None

    logging.basicConfig(level=level)

    sys.exit(main(args))
