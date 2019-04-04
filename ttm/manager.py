#! /usr/bin/python
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

from __future__ import print_function

import ToolBase
import logging
import re
import yaml
from xml.etree import cElementTree as ET
from osclib.stagingapi import StagingAPI
try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

from ttm.totest import ToTest

class NotFoundException(Exception):
    pass

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
            result = re.match(r'.*-(?:Build|Snapshot)([0-9.]+)(?:-Media.*\.iso|\.docker\.tar\.xz|\.raw\.xz)', binary)
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

    # we don't lock the access to this attribute as the times these
    # snapshots are greatly different
    def update_status(self, status, snapshot):
        status_dict = self.get_status_dict()
        if status_dict.get(status, '') != snapshot:
            status_dict[status] = snapshot
            text = yaml.safe_dump(status_dict)
            self.api.attribute_value_save('ToTestManagerStatus', text)

    def get_status_dict(self):
        text = self.api.attribute_value_load('ToTestManagerStatus')
        if text:
            return yaml.safe_load(text)
        return dict()

    def get_status(self, status):
        return self.get_status_dict().get(status, '')
