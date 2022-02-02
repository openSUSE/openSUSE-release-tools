# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import ToolBase
import logging
import re
import yaml
from enum import IntEnum
from xml.etree import cElementTree as ET
from osclib.stagingapi import StagingAPI
from urllib.error import HTTPError
from ttm.totest import ToTest

class NotFoundException(Exception):
    pass

class QAResult(IntEnum):
    inprogress = 1
    failed = 2
    passed = 3

    def __str__(self):
        if self == QAResult.inprogress:
            return 'inprogress'
        elif self == QAResult.failed:
            return 'failed'
        else:
            return 'passed'

class ToTestManager(ToolBase.ToolBase):

    def __init__(self, tool):
        ToolBase.ToolBase.__init__(self)
        # copy attributes
        self.logger = logging.getLogger(__name__)
        self.apiurl = tool.apiurl
        self.debug = tool.debug
        self.caching = tool.caching
        self.dryrun = tool.dryrun

    def setup(self, project):
        self.project = ToTest(project, self.apiurl)
        self.api = StagingAPI(self.apiurl, project=project)

    def version_file(self, target):
        return 'version_%s' % target

    def write_version_to_dashboard(self, target, version):
        if self.dryrun or self.project.do_not_release:
            return
        self.api.pseudometa_file_ensure(self.version_file(target), version,
                                        comment='Update version')

    def current_qa_version(self):
        return self.api.pseudometa_file_load(self.version_file('totest'))

    def iso_build_version(self, project, tree, repo=None, arch=None):
        for binary in self.binaries_of_product(project, tree, repo=repo, arch=arch):
            result = re.match(r'.*-(?:Build|Snapshot)([0-9.]+)(?:-Media.*\.iso|\.docker\.tar\.xz|\.tar\.xz|\.raw\.xz|\.appx)', binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s iso version" % project)

    def version_from_totest_project(self):
        if len(self.project.main_products):
            return self.iso_build_version(self.project.test_project, self.project.main_products[0])

        return self.iso_build_version(self.project.test_project, self.project.image_products[0].package,
                                      arch=self.project.image_products[0].archs[0])

    def binaries_of_product(self, project, product, repo=None, arch=None):
        if repo is None:
            repo = self.project.product_repo
        if arch is None:
            arch = self.project.product_arch

        url = self.api.makeurl(['build', project, repo, arch, product])
        try:
            f = self.api.retried_GET(url)
        except HTTPError:
            return []

        ret = []
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            ret.append(binary.get('filename'))

        return ret

    def ftp_build_version(self, project, tree):
        for binary in self.binaries_of_product(project, tree):
            result = re.match(r'.*-Build(.*)-Media1.report', binary)
            if result:
                return result.group(1)
        raise NotFoundException("can't find %s ftp version" % project)

    # make sure to update the attribute as atomic as possible - as such
    # only update the snapshot and don't erase anything else. The snapshots
    # have very different update times within the pipeline, so there is
    # normally no chance that releaser and publisher overwrite states
    def update_status(self, status, snapshot):
        status_dict = self.get_status_dict()
        if self.dryrun:
            self.logger.info('setting {} snapshot to {}'.format(status, snapshot))
            return
        if status_dict.get(status) != snapshot:
            status_dict[status] = snapshot
            text = yaml.safe_dump(status_dict)
            self.api.attribute_value_save('ToTestManagerStatus', text)

    def get_status_dict(self):
        text = self.api.attribute_value_load('ToTestManagerStatus')
        if text:
            return yaml.safe_load(text)
        return dict()

    def get_status(self, status):
        return self.get_status_dict().get(status)

    def get_product_version(self):
        return self.api.attribute_value_load('ProductVersion')

    def release_package(self, project, package, set_release=None, repository=None,
                         target_project=None, target_repository=None):
        query = {'cmd': 'release'}

        if set_release:
            query['setrelease'] = set_release

        if repository is not None:
            query['repository'] = repository

        if target_project is not None:
            # Both need to be set
            query['target_project'] = target_project
            query['target_repository'] = target_repository

        baseurl = ['source', project, package]

        url = self.api.makeurl(baseurl, query=query)
        if self.dryrun or self.project.do_not_release:
            self.logger.info('release %s/%s (%s)' % (project, package, query))
        else:
            self.api.retried_POST(url)

    def all_repos_done(self, project, codes=None):
        """Check the build result of the project and only return True if all
        repos of that project are either published or unpublished

        """

        # coolo's experience says that 'finished' won't be
        # sufficient here, so don't try to add it :-)
        codes = ['published', 'unpublished'] if not codes else codes

        url = self.api.makeurl(
            ['build', project, '_result'], {'code': 'failed'})
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        ready = True
        for repo in root.findall('result'):
            # ignore ports. 'factory' is used by arm for repos that are not
            # meant to use the totest manager.
            if repo.get('repository') in ('ports', 'factory', 'images_staging'):
                continue
            if repo.get('dirty') == 'true':
                self.logger.info('%s %s %s -> %s' % (repo.get('project'),
                                                repo.get('repository'), repo.get('arch'), 'dirty'))
                ready = False
            if repo.get('code') not in codes:
                self.logger.info('%s %s %s -> %s' % (repo.get('project'),
                                                repo.get('repository'), repo.get('arch'), repo.get('code')))
                ready = False
        return ready
