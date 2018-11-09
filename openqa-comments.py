#!/usr/bin/python

import argparse
from datetime import datetime, timedelta
from collections import defaultdict
import json

from osclib.comments import CommentAPI
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

import osc

MARGIN_HOURS = 4
MAX_LINES = 6


class OpenQAReport(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(api.apiurl)

    def _package_url(self, package):
        link = '/package/live_build_log/%s/%s/%s/%s'
        link = link % (package['project'],
                       package['package'],
                       package['repository'],
                       package['arch'])
        text = '[%s](%s)' % (package['arch'], link)
        return text

    def old_enough(self, _date):
        time_delta = datetime.utcnow() - _date
        safe_margin = timedelta(hours=MARGIN_HOURS)
        return safe_margin <= time_delta

    def is_there_openqa_comment(self, project):
        """Return True if there is a previous comment."""
        signature = '<!-- openQA status -->'
        comments = self.comment.get_comments(project_name=project)
        comment = [c for c in comments.values() if signature in c['comment']]
        return len(comment) > 0

    def update_status_comment(self, project, report, force=False):
        signature = '<!-- openQA status -->'
        report = '%s\n%s' % (signature, str(report))

        write_comment = False

        comments = self.comment.get_comments(project_name=project)
        comment = [c for c in comments.values() if signature in c['comment']]
        if comment and len(comment) > 1:
            print 'ERROR. There are more than one openQA status comment in %s' % project
            # for c in comment:
            #     self.comment.delete(c['id'])
            # write_comment = True
        elif comment and comment[0]['comment'] != report and self.old_enough(comment[0]['when']):
            self.comment.delete(comment[0]['id'])
            write_comment = True
        elif not comment:
            write_comment = True

        if write_comment or force:
            if osc.conf.config['debug']:
                print 'Updating comment'
            self.comment.add_comment(project_name=project, comment=report)

    def _report_broken_packages(self, info):
        broken_package_status = info['broken_packages']

        # Group packages by name
        groups = defaultdict(list)
        for package in broken_package_status:
            groups[package['package']].append(package)

        failing_lines = [
            '* Build failed %s (%s)' % (key, ', '.join(self._package_url(p) for p in value))
            for key, value in groups.iteritems()
        ]

        report = '\n'.join(failing_lines[:MAX_LINES])
        if len(failing_lines) > MAX_LINES:
            report += '* and more (%s) ...' % (len(failing_lines) - MAX_LINES)
        return report

    def report_checks(self, info):
        failing_lines, green_lines = [], []

        links_state = {}
        for check in info['checks']:
            links_state.setdefault(check['state'], [])
            links_state[check['state']].append('[{}]({})'.format(check['name'], check['url']))

        lines = []
        failure = False
        for state, links in links_state.items():
            if len(links) > MAX_LINES:
                extra = len(links) - MAX_LINES
                links = links[:MAX_LINES]
                links.append('and {} more...'.format(extra))

            lines.append('- {}'.format(state))
            if state != 'success':
                lines.extend(['  - {}'.format(link) for link in links])
                failure = True
            else:
                lines[-1] += ': {}'.format(', '.join(links))

        return '\n'.join(lines).strip(), failure

    def report(self, project, aggregate=True):
        info = self.api.project_status(project, aggregate)

        # Some staging projects do not have info like
        # openSUSE:Factory:Staging:Gcc49
        if not info:
            return

        if info['overall_state'] == 'empty':
            return

        # The 'unacceptable' status means that the project will be
        # replaced soon. Better do not disturb with noise.
        if info['overall_state'] == 'unacceptable':
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
            if report:
                if osc.conf.config['debug']:
                    print project
                    print '-' * len(project)
                    print report
                self.update_status_comment(project, report)
            elif not info['overall_state'] == 'acceptable' and self.is_there_openqa_comment(project):
                report = 'Congratulations! All fine now.'
                if osc.conf.config['debug']:
                    print project
                    print '-' * len(project)
                    print report
                self.update_status_comment(project, report, force=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Publish openQA status in staging projects')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project')
    parser.add_argument('-f', '--force', action='store_true', default=False,
                        help='force a comment to be written')
    parser.add_argument('-p', '--project', type=str, default='openSUSE:Factory',
                        help='project to check (ex. openSUSE:Factory, openSUSE:Leap:15.1)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config()
    osc.conf.config['debug'] = args.debug

    if args.force:
        MARGIN_HOURS = 0

    apiurl = osc.conf.config['apiurl']
    Config(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)
    openQA = OpenQAReport(api)

    if args.staging:
        openQA.report(api.prj_from_letter(args.staging), False)
    else:
        for staging in api.get_staging_projects():
            openQA.report(staging)
