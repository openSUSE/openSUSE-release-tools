#!/usr/bin/python3

import argparse
import logging
import os
import sys
from urllib.error import HTTPError

import osc.core
from lxml import etree as ET

from osclib.conf import Config

from osclib.stagingapi import StagingAPI

from staginginstallchecker.installchecker import InstallChecker

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))


class OBSInstallChecker(InstallChecker):
    def staging(self, project, repository, force=False, devel=False):
        # fetch the build ids at the beginning - mirroring takes a while
        buildids = {}
        try:
            architectures = self.target_archs(project, repository)
        except HTTPError as e:
            if e.code == 404:
                # adi disappear all the time, so don't worry
                return False
            raise e

        all_done = True
        for arch in architectures:
            pra = f'{project}/{repository}/{arch}'
            buildid = self.buildid(project, repository, arch)
            if not buildid:
                self.logger.error(f'No build ID in {pra}')
                return False
            buildids[arch] = buildid
            url = self.report_url(project, repository, arch, buildid)
            try:
                root = ET.parse(osc.core.http_GET(url)).getroot()
                check = root.find('check[@name="installcheck"]/state')
                if check is not None and check.text != 'pending':
                    self.logger.info(f'{pra} already "{check.text}", ignoring')
                else:
                    all_done = False
            except HTTPError:
                self.logger.info(f'{pra} has no status report')
                all_done = False

        if all_done and not force:
            return True

        result = self.staging_installcheck(project, repository, architectures, devel=devel)

        if not devel:
            if result.success:
                self.report_state('success', self.gocd_url(), project, repository, buildids)
            else:
                result.comment.insert(0, f'Generated from {self.gocd_url()}\n')
                self.report_state('failure', self.upload_failure(project, result.comment), project, repository, buildids)
                self.logger.warning(f'Not accepting {project}')

        return result.success

    def upload_failure(self, project, comment):
        print(project, '\n'.join(comment))
        url = self.api.makeurl(['source', 'home:repo-checker', 'reports', project])
        osc.core.http_PUT(url, data='\n'.join(comment))

        url = self.api.apiurl.replace('api.', 'build.')
        return f'{url}/package/view_file/home:repo-checker/reports/{project}'

    def report_state(self, state, report_url, project, repository, buildids):
        architectures = self.target_archs(project, repository)
        for arch in architectures:
            self.report_pipeline(state, report_url, project, repository, arch, buildids[arch])

    def gocd_url(self):
        if not os.environ.get('GO_SERVER_URL'):
            # placeholder :)
            return 'http://stephan.kulow.org/'
        report_url = os.environ.get('GO_SERVER_URL').replace(':8154', '')
        return report_url + '/tab/build/detail/{}/{}/{}/{}/{}#tab-console'.format(os.environ.get('GO_PIPELINE_NAME'),
                                                                                  os.environ.get('GO_PIPELINE_COUNTER'),
                                                                                  os.environ.get('GO_STAGE_NAME'),
                                                                                  os.environ.get('GO_STAGE_COUNTER'),
                                                                                  os.environ.get('GO_JOB_NAME'))

    def buildid(self, project, repository, architecture):
        url = self.api.makeurl(['build', project, repository, architecture], {'view': 'status'})
        root = ET.parse(osc.core.http_GET(url)).getroot()
        buildid = root.find('buildid')
        if buildid is None:
            return False
        return buildid.text

    def report_url(self, project, repository, architecture, buildid):
        return self.api.makeurl(['status_reports', 'built', project,
                                 repository, architecture, 'reports', buildid])

    def report_pipeline(self, state, report_url, project, repository, architecture, buildid):
        url = self.report_url(project, repository, architecture, buildid)
        name = 'installcheck'
        xml = self.check_xml(report_url, state, name)
        try:
            osc.core.http_POST(url, data=xml)
        except HTTPError:
            print('failed to post status to ' + url)
            sys.exit(1)

    def check_xml(self, url, state, name):
        check = ET.Element('check')
        if url:
            se = ET.SubElement(check, 'url')
            se.text = url
        se = ET.SubElement(check, 'state')
        se.text = state
        se = ET.SubElement(check, 'name')
        se.text = name
        return ET.tostring(check)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Do an installcheck on staging project')
    parser.add_argument('-s', '--staging', type=str, default=None,
                        help='staging project')
    parser.add_argument('--devel', type=str, default=None,
                        help='devel project (ex GNOME:Factory)')
    parser.add_argument('-r', '--repository', type=str, default=None,
                        help='repository to check, if not specified, use the staging configuration')
    parser.add_argument('-p', '--project', type=str, default='openSUSE:Factory',
                        help='project to check (ex. openSUSE:Factory, openSUSE:Leap:15.1)')
    parser.add_argument('-d', '--debug', action='store_true', default=False,
                        help='enable debug information')
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    apiurl = osc.conf.config['apiurl']
    config = Config.get(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)
    if not args.repository:
        args.repository = api.cmain_repo
    staging_report = OBSInstallChecker(api, config)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if args.staging:
        if not staging_report.staging(api.prj_from_short(args.staging), repository=args.repository, force=True):
            sys.exit(1)
    elif args.devel:
        if not staging_report.staging(args.devel, repository=args.repository, force=True, devel=True):
            sys.exit(1)
    else:
        for staging in api.get_staging_projects():
            if api.is_adi_project(staging):
                staging_report.staging(staging, repository=args.repository)
    sys.exit(0)
