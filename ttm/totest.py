#!/usr/bin/python2
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

class ImageProduct(object):
    def __init__(self, package, archs):
        self.package = package
        self.archs = archs

class ToTest(object):

    """Base class to store the basic interface"""

    def __init__(self, project):
        self.name = project

        # set the defaults
        self.do_not_release = False
        self.need_same_build_number = False
        self.set_snapshot_number = False
        self.is_image_product = False

        self.product_repo = 'images'
        self.product_arch = 'local'
        self.livecd_repo = 'images'
        self.totest_container_repo = 'containers'

        self.main_products = []
        self.ftp_products = []
        self.container_products = []
        self.livecd_products = []
        self.image_products = []

        self.test_subproject = 'ToTest'

        self.test_project = '%s:%s' % (self.project, self.test_subproject)
        self.project_base = project.split(':')[0]

