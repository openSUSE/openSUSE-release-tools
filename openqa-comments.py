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

    def _openQA_url(self, job):
        test_name = job['name'].split('-')[-1]
        link = '%s/tests/%s' % (self.api.copenqa, job['id'])
        text = '[%s](%s)' % (test_name, link)
        return text

    def _openQA_module_url(self, job, module):
        link = '%s/tests/%s/modules/%s/steps/1' % (
            self.api.copenqa, job['id'], module['name']
        )
        text = '[%s](%s)' % (module['name'], link)
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

    def _report_openQA(self, info):
        failing_lines, green_lines = [], []

        openQA_status = info['openqa_jobs']
        for job in openQA_status:
            test_name = job['name'].split('-')[-1]
            fails = [
                '  * %s (%s)' % (test_name, self._openQA_module_url(job, module))
                for module in job['modules'] if module['result'] == 'failed'
            ]

            if fails:
                failing_lines.extend(fails)
            else:
                green_lines.append(self._openQA_url(job))

        failing_report, green_report = '', ''
        if failing_lines:
            failing_report = '* Failing tests:\n' + '\n'.join(failing_lines[:MAX_LINES])
            if len(failing_lines) > MAX_LINES:
                failing_report += '\n  * and more (%s) ...' % (len(failing_lines) - MAX_LINES)
        if green_lines:
            green_report = '* Succeeding tests:' + ', '.join(green_lines[:MAX_LINES])
            if len(green_lines) > MAX_LINES:
                green_report += ', and more (%s) ...' % (len(green_lines) - MAX_LINES)

        return '\n'.join((failing_report, green_report)).strip(), bool(failing_lines)

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
        report_openQA, some_openqa_fail = self._report_openQA(info)

        if report_broken_packages or some_openqa_fail:
            if report_broken_packages:
                report_broken_packages = 'Broken:\n\n' + report_broken_packages
            if report_openQA:
                report_openQA = 'openQA:\n\n' + report_openQA
            report = '\n\n'.join((report_broken_packages, report_openQA))
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
