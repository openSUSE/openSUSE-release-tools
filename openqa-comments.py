# Copyright (C) 2014 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import argparse
from datetime import datetime, timedelta
import json

from osclib.comments import CommentAPI
from osclib.stagingapi import StagingAPI

import osc


class OpenQAReport(object):
    def __init__(self, api):
        self.api = api
        self.comment = CommentAPI(self.api.apiurl)

    def _openQA_url(self, job):
        test_name = job['name'].split('-')[-1] 
        link = 'https://openqa.opensuse.org/tests/%s' % job['id']
        text = '[%s](%s)' % (test_name, link)
        return text

    def _openQA_module_url(self, job, module):
        link = 'https://openqa.opensuse.org/tests/%s/modules/%s/steps/1' % (
            job['id'], module['name']
        )
        text = '[%s](%s)' % (module['name'], link)
        return text

    def old_enough(self, _date):
        
        time_delta = datetime.utcnow() - _date
        safe_margin = timedelta(hours=4)
        return safe_margin <= time_delta

    def get_openQA_status(self, project):
        _prefix = 'openSUSE:{}:Staging:'.format(self.api.opensuse)
        if project.startswith(_prefix):
            project = project.replace(_prefix, '')

        query = {'format': 'json'}
        url = api.makeurl(('project', 'staging_projects',
                           'openSUSE:%s' % api.opensuse, project), query=query)
        info = json.load(api.retried_GET(url))
        return info['openqa_jobs'] if 'openqa_jobs' in info else None

    def update_openQA_status_comment(self, project, report):
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

        if write_comment:
            self.comment.add_comment(project_name=project, comment=report)

    def report(self, project):
        report_lines = []

        openQA_status = self.get_openQA_status(project)
        for job in openQA_status:
            modules = job['modules']
            report_lines.append('* openQA test: %s' % self._openQA_url(job))

            fails = [
                '  * %s is failing' % self._openQA_module_url(job, module)
                for module in modules if module['result'] == 'fail'
            ]

            if fails:
                report_lines.extend(fails)
            else:
                report_lines.append('  * Completely green!')

        report = '\n'.join(report_lines)
        if report:
            self.update_openQA_status_comment(project, report)
        

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Command to publish openQA status in Staging projects')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project letter')
    parser.add_argument('-p', '--project', type=str, default='Factory',
                        help='openSUSE version to make the check (Factory, 13.2)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')

    args = parser.parse_args()

    osc.conf.get_config()
    osc.conf.config['debug'] = args.debug

    api = StagingAPI(osc.conf.config['apiurl'], args.project)
    openQA = OpenQAReport(api)

    if args.staging:
         openQA.report(api.prj_from_letter(args.staging))
    else:
        for staging in api.get_staging_projects():
            openQA.report(staging)
