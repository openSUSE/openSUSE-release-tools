#!/usr/bin/python3
# (c) 2023 fvogt@suse.de
# GPL-2.0-or-later

# This is a "mini ttm" for the BCI repo. Differences:
# * No :ToTest staging area
# * Only the repo is built, so it needs BCI specific openQA queries
# * The publishing location is arch specific
# * Uses a token for releasing to the publishing project


from typing import List, Optional
from typing_extensions import Literal, TypedDict
import cmdln
import logging
import ToolBase
import requests
import sys
import time
import re
from lxml import etree as ET
from openqa_client.client import OpenQA_Client
from osc.core import makeurl
from osc.connection import http_GET
from random import randint

_SLE_VERSION_T = Literal['15-SP3', '15-SP4', '15-SP5', '15-SP6']


class BCIRepoPublisher(ToolBase.ToolBase):
    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.logger = logging.getLogger(__name__)
        self.openqa = OpenQA_Client(server='https://openqa.suse.de')

    def version_of_product(self, project: str, package: str, repo: str, arch: str) -> str:
        """Get the build version of the given product build, based on the binary name."""
        url = makeurl(self.apiurl, ['build', project, repo, arch, package])
        root = ET.parse(http_GET(url)).getroot()
        for binary in root.findall('binary'):
            result = re.match(r'.*-Build(.*)-Media1.report', binary.get('filename'))
            if result:
                return result.group(1)

        raise RuntimeError(f"Failed to get version of {project}/{package}")

    def mtime_of_product(self, project: str, package: str, repo: str, arch: str) -> int:
        """Get the build time stamp of the given product, based on _buildenv."""
        url = makeurl(self.apiurl, ['build', project, repo, arch, package])
        root = ET.parse(http_GET(url)).getroot()
        mtime = root.xpath('/binarylist/binary[@filename = "_buildenv"]/@mtime')
        return mtime[0]

    def openqa_jobs_for_product(self, arch, version, build):
        """Query openQA for all relevant jobs"""
        values = {
            'group': 'BCI repo',
            'flavor': 'BCI-Repo-Updates',
            'arch': arch,
            'version': version,
            'build': build,
            'scope': 'current',
            'latest': '1',
        }
        return self.openqa.openqa_request('GET', 'jobs', values)['jobs']

    def is_repo_published(self, project: str, repo: str, arch: Optional[str] = None) -> bool:
        """Validate that the given prj/repo is fully published and all builds
        have succeeded. If an architecture is provided, then only that
        architecture is considered for checking the publishing & build state.

        """
        result_filter = {'view': 'summary', 'repository': repo}
        if arch:
            result_filter['arch'] = arch
        url = makeurl(self.apiurl, ['build', project, '_result'], result_filter)
        root = ET.parse(http_GET(url)).getroot()
        for result in root.findall('result'):
            if result.get('dirty', 'false') != 'false':
                return False
            if result.get('code') != 'published' or result.get('state') != 'published':
                return False

        for statuscount in root.findall('statuscount'):
            if statuscount.get('code') not in ('succeeded', 'disabled', 'excluded'):
                return False

        return True

    def run(self, version, token=None):
        build_prj = f'SUSE:SLE-{version}:Update:BCI'

        if not self.is_repo_published(build_prj, 'images', 'local'):
            self.logger.info(f'{build_prj}/images not successfully built')
            return

        # Build the list of packages with metainfo
        packages = []
        # List of packages that have passed openQA
        openqa_passed_packages = []
        # As long as it's the same everywhere, hardcoding this list here
        # is easier and safer than trying to derive it from the package list.
        for arch in ('aarch64', 'ppc64le', 's390x', 'x86_64'):
            packages.append({
                'arch': arch,
                'name': f'000product:SLE_BCI-ftp-POOL-{arch}',
                'build_prj': build_prj,
                'publish_prj': f'SUSE:Products:SLE-BCI:{version}:{arch}'
            })

        # Fetch the build numbers of built products.
        # After release, the BuildXXX part vanishes, so the mtime has to be
        # used instead for comparing built and published binaries.
        for pkg in packages:
            pkg['built_version'] = self.version_of_product(pkg['build_prj'], pkg['name'],
                                                           'images', 'local')
            pkg['built_mtime'] = self.mtime_of_product(pkg['build_prj'], pkg['name'],
                                                       'images', 'local')
            pkg['published_mtime'] = self.mtime_of_product(pkg['publish_prj'], pkg['name'],
                                                           'images', 'local')

        # Verify that the builds for all archs are in sync
        built_versions = {pkg['built_version'] for pkg in packages}
        if len(built_versions) != 1:
            # This should not be the case if everything is built and idle
            self.logger.warning(f'Different builds found - not releasing: {packages}')
            return

        # Compare versions
        newer_version_available = [int(pkg['built_mtime']) > int(pkg['published_mtime'])
                                   for pkg in packages]
        if not any(newer_version_available):
            self.logger.info('Current build already published, nothing to do.')
            return

        # If the last published build is less than a day old, don't publish
        newest_published_mtime = max([int(pkg['published_mtime']) for pkg in packages])
        published_build_age_hours = int(time.time() - newest_published_mtime) // (60 * 60)
        if published_build_age_hours < 24:
            self.logger.info('Current published build less than a day old '
                             f'({published_build_age_hours}h).')
            return

        # Check openQA results
        mandatory_arches = ('aarch64', 'x86_64')
        for pkg in packages:
            passed = 0
            pending = 0
            failed = 0
            for job in self.openqa_jobs_for_product(arch=pkg['arch'], version=version,
                                                    build=pkg['built_version']):
                if job['result'] in ('passed', 'softfailed'):
                    passed += 1
                elif job['result'] == 'none':
                    self.logger.info(f'https://openqa.suse.de/tests/{job["id"]} pending')
                    pending += 1
                else:
                    self.logger.warning(f'https://openqa.suse.de/tests/{job["id"]} failed')
                    failed += 1
            if pending or failed:
                self.logger.info(f'openQA did not (yet) pass for {pkg["name"]}: {passed}/{pending}/{failed}')
                continue
            if passed == 0 and pkg['arch'] in mandatory_arches:
                self.logger.info('No positive result from openQA (yet)')
                return
            openqa_passed_packages.append(pkg)

        # Trigger publishing
        if token is None:
            self.logger.warning(f'Would publish {[pkg["name"] for pkg in openqa_passed_packages]}, but no token specified')
            return

        for pkg in openqa_passed_packages:
            self.logger.info(f'Releasing {pkg["name"]}...')
            params = {
                'project': pkg['build_prj'], 'package': pkg['name'],
                'filter_source_repository': 'images',
                'targetproject': pkg['publish_prj'], 'targetrepository': 'images'
            }
            url = makeurl(self.apiurl, ['trigger', 'release'], params)
            # No bindings for using tokens yet, so do the request manually
            req = requests.post(url, headers={'Authorization': f'Token {token}'})
            if req.status_code != 200:
                raise RuntimeError(f'Releasing failed: {req.text}')

        self.logger.info('Waiting for publishing to finish')
        for pkg in openqa_passed_packages:
            while not self.is_repo_published(pkg['publish_prj'], 'images'):
                self.logger.debug(f'Waiting for {pkg["publish_prj"]}')
                time.sleep(randint(10, 30))


class CommandLineInterface(ToolBase.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def setup_tool(self):
        tool = BCIRepoPublisher()
        if self.options.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.options.verbose:
            logging.basicConfig(level=logging.INFO)

        return tool

    @cmdln.option('--token', help='The token for publishing. Does a dry run if not given.')
    def do_run(self, subcmd, opts, project):
        """${cmd_name}: run BCI repo publisher for the project, e.g. 15-SP5.

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.run(project, token=opts.token)


if __name__ == "__main__":
    cli = CommandLineInterface()
    sys.exit(cli.main())
