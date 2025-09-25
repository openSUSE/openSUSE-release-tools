# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# (C) 2014 aplanas@suse.de, openSUSE.org
# (C) 2014 coolo@suse.de, openSUSE.org
# (C) 2017 okurz@suse.de, openSUSE.org
# (C) 2018 dheidler@suse.de, openSUSE.org
# Distribute under GPLv2 or GPLv3

import re
from collections import defaultdict
from lxml import etree as ET

from ttm.manager import ToTestManager, NotFoundException, QAResult


class ToTestReleaser(ToTestManager):

    def __init__(self, tool):
        ToTestManager.__init__(self, tool)

    def setup(self, project):
        super(ToTestReleaser, self).setup(project)

    def release(self, project, force=False):
        self.setup(project)

        testing_snapshot = self.get_status('testing')
        if not testing_snapshot and not force:
            self.logger.debug("No snapshot in testing, waiting for publisher to tell us")
            return None
        new_snapshot = self.version_from_project()

        if not force:
            # not overwriting
            if new_snapshot == testing_snapshot:
                self.logger.debug('no change in snapshot version')
                return None

            if testing_snapshot != self.get_status('failed') and testing_snapshot != self.get_status('published'):
                self.logger.debug(f'Snapshot {testing_snapshot} is still in progress')
                return QAResult.inprogress

            self.logger.info('testing snapshot %s', testing_snapshot)
            self.logger.debug('new snapshot %s', new_snapshot)

            if not self.is_snapshotable():
                self.logger.debug('not snapshotable')
                return QAResult.failed

            if not self.all_built_products_in_config():
                self.logger.debug('config incomplete')
                return QAResult.failed

        self.update_totest(new_snapshot)
        self.update_status('testing', new_snapshot)
        self.update_status('failed', '')
        self.write_version_to_dashboard('totest', new_snapshot)
        return QAResult.passed

    def release_version(self):
        url = self.api.makeurl(['build', self.project.name, 'standard', self.project.arch,
                                f'000release-packages:{self.project.base}-release'])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            binary = binary.get('filename', '')
            result = re.match(r'.*-([^-]*)-[^-]*.src.rpm', binary)
            if result:
                return result.group(1)

        raise NotFoundException(f"can't find {self.project.name} version")

    def version_from_project(self):
        if not self.project.take_source_from_product:
            return self.release_version()

        first_product = self.project.products[0]
        return self.build_version(first_product.build_prj, first_product.package,
                                  first_product.build_repo, first_product.archs[0])

    def package_ok(self, prjresult, product, arch):
        """Checks one product/arch in a project and returns True if it's succeeded"""

        status = prjresult.xpath(f'result[@repository="{product.build_repo}"][@arch="{arch}"]/'
                                 f'status[@package="{product.package}"]')

        failed = [s for s in status if s.get('code') != 'succeeded']
        if len(failed):
            self.logger.info(
                f"{product.build_prj} {product.package} {product.build_repo} {arch} -> {failed[0].get('code')}")
            return False

        succeeded = [s for s in status if s.get('code') == 'succeeded']
        if not len(succeeded):
            self.logger.info(f'No "succeeded" for {product.build_prj} {product.package} {product.build_repo} {arch}')
            return False

        if product.max_size is None:
            return True

        url = self.api.makeurl(['build', product.build_prj, product.build_repo, arch, product.package])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            if not binary.get('filename', '').endswith('.iso'):
                continue
            isosize = int(binary.get('size', 0))
            if isosize > product.max_size:
                self.logger.error('%s %s %s %s: %s' % (
                    product.build_prj, product.package, product.build_repo, arch, 'too large by %s bytes' % (isosize - product.max_size)))
                return False

        return True

    def all_built_products_in_config(self):
        """Verify that all succeeded products are mentioned in the ttm config"""

        # Dict of products per prj/repo, e.g.
        # {('openSUSE:Leap:15.6:Images', 'images'): {'livecd-leap-gnome': ['aarch64', 'x86_64'], ...}}
        products_for_prj_repo = defaultdict(dict)
        for p in self.project.products:
            products_for_prj_repo[(p.build_prj, p.build_repo)][p.package] = p.archs

        all_found = True
        for (prj, repo), products in products_for_prj_repo.items():
            all_found = self.verify_package_list_complete(prj, repo, products) and all_found

        return all_found

    def verify_package_list_complete(self, project, repository, product_archs):
        """Loop through all successfully built products and check whether they
           are part of product_archs (e.g. {'foo:ftp': ['local'], some-image': ['x86_64'], ...})"""

        # Don't return false early, to show all errors at once
        all_found = True

        # Get all results for the product repo from OBS
        url = self.api.makeurl(['build', project, "_result"],
                               {'repository': repository,
                                'multibuild': 1})
        f = self.api.retried_GET(url)
        resultlist = ET.parse(f).getroot()

        for result in resultlist.findall('result'):
            arch = result.get('arch')
            for package in result.findall('status[@code="succeeded"]'):
                packagename = package.get('package')
                released_archs = None
                if packagename in product_archs:
                    released_archs = product_archs[packagename]
                elif ':' in packagename:
                    # For multibuild, it's enough to release the container
                    multibuildcontainer = packagename.split(':')[0]
                    if multibuildcontainer in product_archs:
                        released_archs = product_archs[multibuildcontainer]
                        # Ignore the arch check for multibuild containers,
                        # as it might not build for the same archs as all flavors.
                        continue

                if released_archs is None:
                    self.logger.error("%s is built for %s, but not mentioned as product" % (
                        packagename, arch))
                    all_found = False
                elif arch not in released_archs:
                    self.logger.error("%s is built for %s, but that arch is not mentioned" % (
                        packagename, arch))
                    all_found = False

        return all_found

    def is_snapshotable(self):
        """Check various conditions required for factory to be snapshotable"""

        all_ok = True

        # Collect a list of projects to check
        projects = set([p.build_prj for p in self.project.products])
        for prj in projects:
            if not self.all_repos_done(prj):
                all_ok = False
                continue

            resultxml = self.api.retried_GET(self.api.makeurl(['build', prj, '_result']))
            prjresult = ET.parse(resultxml).getroot()

            for product in self.project.products:
                if product.build_prj != prj:
                    continue

                for arch in product.archs:
                    if not self.package_ok(prjresult, product, arch):
                        all_ok = False

        if not all_ok:
            return False

        # The FTP tree isn't released with setrelease, so it needs to contain
        # the product version already.
        product_version = self.get_product_version()
        if product_version is not None:
            for product in [p for p in self.project.products if p.needs_to_contain_product_version]:
                for binary in self.binaries_of_product(product.build_prj, product.package,
                                                       repo=product.build_repo, arch=product.archs[0]):
                    if binary.endswith('.report') and product_version not in binary:
                        self.logger.debug(f'{binary} in {product} does not include {product_version}')
                        return False

        return True

    def update_totest(self, snapshot=None):
        # omit snapshot, we don't want to rename on release
        if not self.project.set_snapshot_number:
            snapshot = None
        if snapshot:
            release = self.project.snapshot_number_prefix + snapshot
            self.logger.info(f'Updating snapshot {snapshot}')
        else:
            release = None
        if not (self.dryrun or self.project.do_not_release):
            prj_repo_to_publish_disable = \
                set([(p.release_prj, p.release_repo) for p in self.project.products if not p.publish_using_release])
            for prj, repo in prj_repo_to_publish_disable:
                self.api.switch_flag_in_prj(prj, flag='publish', state='disable', repository=repo)

        for p in self.project.products:
            self.release_package(p.build_prj, p.package,
                                 set_release=release if p.release_set_version else None,
                                 repository=p.build_repo,
                                 target_project=p.release_prj,
                                 target_repository=p.release_repo)
