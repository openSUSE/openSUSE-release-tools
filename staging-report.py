#!/usr/bin/python3

import argparse
from datetime import datetime, timedelta
from collections import defaultdict

from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
import osc

MARGIN_HOURS = 4
MAX_LINES = 6
MARKER = 'StagingReport'


class StagingReport(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(api.apiurl)

    def _package_url(self, package):
        link = '/package/live_build_log/%s/%s/%s/%s'
        link = link % (package.get('project'),
                       package.get('package'),
                       package.get('repository'),
                       package.get('arch'))
        text = f"[{package.get('arch')}]({link})"
        return text

    def old_enough(self, _date):
        time_delta = datetime.utcnow() - _date
        safe_margin = timedelta(hours=MARGIN_HOURS)
        return safe_margin <= time_delta

    def update_status_comment(self, project, report, force=False, only_replace=False):
        report = self.comment.add_marker(report, MARKER)
        comments = self.comment.get_comments(project_name=project)
        comment, _ = self.comment.comment_find(comments, MARKER)
        if comment:
            write_comment = (report != comment['comment'] and self.old_enough(comment['when']))
        else:
            write_comment = not only_replace

        if write_comment or force:
            if osc.conf.config['debug']:
                print('Updating comment')
            if comment:
                self.comment.delete(comment['id'])
            self.comment.add_comment(project_name=project, comment=report)

    def _report_broken_packages(self, info):
        # Group packages by name
        groups = defaultdict(list)
        for package in info.findall('broken_packages/package'):
            groups[package.get('package')].append(package)

        failing_lines = [
            f"* Build failed {key} ({', '.join(self._package_url(p) for p in value)})"
            for key, value in groups.items()
        ]

        report = '\n'.join(failing_lines[:MAX_LINES])
        if len(failing_lines) > MAX_LINES:
            report += f'* and more ({len(failing_lines) - MAX_LINES}) ...'
        return report

    def report_checks(self, info):
        links_state = {}
        for check in info.findall('checks/check'):
            state = check.find('state').text
            links_state.setdefault(state, [])
            links_state[state].append(f"[{check.get('name')}]({check.find('url').text})")

        lines = []
        failure = False
        for state, links in links_state.items():
            if len(links) > MAX_LINES:
                extra = len(links) - MAX_LINES
                links = links[:MAX_LINES]
                links.append(f'and {extra} more...')

            lines.append(f'- {state}')
            if state != 'success':
                lines.extend([f'  - {link}' for link in links])
                failure = True
            else:
                lines[-1] += f": {', '.join(links)}"

        return '\n'.join(lines).strip(), failure

    def report(self, project, force=False):
        info = self.api.project_status(project)

        # Do not attempt to process projects without staging info, or projects
        # in a pending state that will change before settling. This avoids
        # intermediate notifications that may end up being spammy and for
        # long-lived stagings where checks may be re-triggered multiple times
        # and thus enter pending state (not seen on first run) which is not
        # useful to report.
        if info is None or not self.api.project_status_final(info):
            return

        report_broken_packages = self._report_broken_packages(info)
        report_checks, check_failure = self.report_checks(info)

        if report_broken_packages or check_failure:
            if report_broken_packages:
                report_broken_packages = 'Broken:\n\n' + report_broken_packages
            if report_checks:
                report_checks = 'Checks:\n\n' + report_checks
            report = '\n\n'.join((report_broken_packages, report_checks))
            report = report.strip()
            only_replace = False
        else:
            report = 'Congratulations! All fine now.'
            only_replace = True

        report = self.cc_list(project, info) + report
        self.update_status_comment(project, report, force=force, only_replace=only_replace)

        if osc.conf.config['debug']:
            print(project)
            print('-' * len(project))
            print(report)

    def cc_list(self, project, info):
        if not self.api.is_adi_project(project):
            return ""
        ccs = set()
        for req in info.findall('staged_requests/request'):
            ccs.add("@" + req.get('creator'))
        str = "Submitters: " + " ".join(sorted(list(ccs))) + "\n\n"
        return str


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Publish report on staging status as comment on staging project')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project')
    parser.add_argument('-f', '--force', action='store_true', default=False,
                        help='force a comment to be written')
    parser.add_argument('-p', '--project', type=str, default='openSUSE:Factory',
                        help='project to check (ex. openSUSE:Factory, openSUSE:Leap:15.1)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']
    Config(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)
    staging_report = StagingReport(api)

    if args.staging:
        staging_report.report(api.prj_from_letter(args.staging), args.force)
    else:
        for staging in api.get_staging_projects():
            staging_report.report(staging, args.force)
