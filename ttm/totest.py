# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import dataclasses
import yaml
import re
from osclib.core import attribute_value_load
from typing import List, Optional


@dataclasses.dataclass
class Product(object):
    """Attributes documented in README.md"""
    package: str
    archs: List[str]  # [totest.product_arch]
    build_prj: str  # Main prj, e.g. openSUSE:Factory
    build_repo: str  # totest.product_repo
    needs_to_contain_product_version: bool  # False
    max_size: Optional[int]  # In B, e.g. 737280000 for CDs
    release_prj: str  # totest.test_project
    release_repo: str  # totest.product_repo
    release_set_version: bool  # False
    publish_using_release: bool  # False

    @staticmethod
    def custom_product(totest, name, **options):
        # Start with defaults
        p = Product(package=name, archs=[totest.product_arch],
                    build_prj=totest.name,
                    build_repo=totest.product_repo,
                    needs_to_contain_product_version=False,
                    max_size=None,
                    release_prj=totest.test_project,
                    release_repo=totest.product_repo,
                    release_set_version=False,
                    publish_using_release=False)

        # Override options
        return Product(**{**dataclasses.asdict(p), **options})

    @staticmethod
    def max_size_default(package):
        """Used by ftp_product and main_product. livecd size is checked
           during build."""
        if re.match(r'.*[-_]mini[-_].*', package):
            return 737280000  # a CD needs to match

        if re.match(r'.*[-_]dvd5[-_].*', package):
            return 4700372992  # a DVD needs to match

        if re.match(r'.*[-_](dvd9[-_]dvd|cd[-_]DVD)[-_].*', package):
            return 8539996159

        # Other types don't have a fixed size limit
        return None

    @staticmethod
    def ftp_product(totest, package):
        """ FTP Repo """
        build_repo = totest.product_repo
        extract_product = re.search(r"(.+)/product_repo:(.+)", package)
        if extract_product:
            package = extract_product.group(1)
            build_repo = extract_product.group(2)

        return Product.custom_product(totest, package,
                                      build_repo=build_repo,
                                      needs_to_contain_product_version=True)

    @staticmethod
    def main_product(totest, package):
        """ Installation DVD """
        return Product.custom_product(totest, package,
                                      max_size=Product.max_size_default(package),
                                      release_set_version=True)

    @staticmethod
    def livecd_product(totest, package, archs):
        """ LiveCD products are like main products, but built in a different
            project. """
        p = Product.main_product(totest, package)
        p.archs = archs
        p.build_prj = f'{totest.name}:Live'
        return p

    @staticmethod
    def image_product(totest, package, archs):
        if totest.same_target_images_repo_for_source_repo:
            build_repo = totest.totest_images_repo
        else:
            build_repo = totest.product_repo

        release_repo = totest.totest_images_repo
        if release_repo is None:
            release_repo = totest.product_repo

        return Product.custom_product(totest, package,
                                      archs=archs,
                                      build_repo=build_repo,
                                      release_repo=release_repo,
                                      release_set_version=True)

    @staticmethod
    def container_product(totest, package, archs):
        """ Containers are built in the same repo as other image products,
            but released into a different repo in :ToTest """
        return Product.custom_product(totest, package,
                                      archs=archs,
                                      release_repo=totest.totest_container_repo,
                                      publish_using_release=True)

    @staticmethod
    def containerfile_product(totest, package, archs):
        """ Containerfile builds are like containers but built in a separate repo"""
        p = Product.container_product(totest, package, archs)
        p.build_repo = 'containerfile'
        return p


class ToTest(object):

    """Base class to store the basic interface"""

    def __init__(self, project, apiurl):
        self.name = project

        # set the defaults
        self.do_not_release = False
        self.set_snapshot_number = False
        self.snapshot_number_prefix = "Snapshot"
        self.take_source_from_product = False
        self.arch = 'x86_64'
        self.openqa_group = None
        self.openqa_server = None
        self.jobs_num = 42
        self.test_subproject = 'ToTest'
        self.base = project.split(':')[0]
        self.products = []

        # Defaults for products
        self.product_repo = 'images'
        self.product_arch = 'local'
        self.test_project = None

        # Defaults for fixed product types
        self.livecd_repo = 'images'
        self.totest_container_repo = 'containers'
        self.same_target_images_repo_for_source_repo = False
        self.totest_images_repo = None

        self.load_config(apiurl)

    def parse_products(self, products, factory):
        parsed = []
        for package in products:
            for key, value in package.items():
                parsed.append(factory(self, key, value))

        return parsed

    def load_config(self, apiurl):
        config_yaml = attribute_value_load(apiurl, self.name, 'ToTestManagerConfig')
        if not config_yaml:
            raise Exception('Failed to read ToTestManagerConfig')

        config = yaml.safe_load(config_yaml)
        raw_products = {}
        for key, value in config.items():
            if key == 'products':
                raw_products = value
            elif hasattr(self, key):
                setattr(self, key, value)
            else:
                raise Exception(f'Unknown config option {key}={value}')

        # Some config migrations
        if self.totest_images_repo is None:
            self.totest_images_repo = self.product_repo

        if self.test_project is None:
            self.test_project = f'{self.name}:{self.test_subproject}'

        # Fill self.products. Order is important: The first product is used for
        # getting the setrelease number for take_source_from_product == True.
        for mp in raw_products.get('main', []):
            self.products += [Product.main_product(self, mp)]

        for key, value in raw_products.get('custom', {}).items():
            self.products += [Product.custom_product(self, key, **value)]

        for mp in raw_products.get('ftp', []):
            self.products += [Product.ftp_product(self, mp)]

        self.products += self.parse_products(raw_products.get('livecds', []), Product.livecd_product)
        self.products += self.parse_products(raw_products.get('images', []), Product.image_product)
        self.products += self.parse_products(raw_products.get('container', []), Product.container_product)
        self.products += self.parse_products(raw_products.get('containerfile', []), Product.containerfile_product)
