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

        if len(self.project.main_products):
            # 000productcompose has ftp built only and the build number
            # offline installer carry over build number from ftp product
            # as well as agama-installer
            if 'productcompose' in self.project.main_products[0] and\
                    'productcompose' in self.project.ftp_products[0]:
                return self.productcompose_build_version(self.project.name, self.project.ftp_products[0],
                                                         repo=self.project.product_repo_overrides.get(self.project.ftp_products[0],
                                                                                                      self.project.product_repo))
            return self.iso_build_version(self.project.name, self.project.main_products[0])

        return self.iso_build_version(self.project.name, self.project.image_products[0].package,
                                      arch=self.project.image_products[0].archs[0])

    def maxsize_for_package(self, package, arch):
        if re.match(r'.*[-_]mini[-_].*', package):
            return 737280000  # a CD needs to match

        if re.match(r'.*[-_]dvd5[-_].*', package):
            return 4700372992  # a DVD needs to match

        if re.match(r'.*[-_](dvd9[-_]dvd|cd[-_]DVD)[-_].*', package):
            return 8539996159

        # Other types don't have a fixed size limit
        return None

    def package_ok(self, prjresult, project, package, repository, arch):
        """Checks one package in a project and returns True if it's succeeded"""

        status = prjresult.xpath(f'result[@repository="{repository}"][@arch="{arch}"]/'
                                 f'status[@package="{package}"]')

        failed = [s for s in status if s.get('code') != 'succeeded']
        if len(failed):
            self.logger.info(
                f"{project} {package} {repository} {arch} -> {failed[0].get('code')}")
            return False

        succeeded = [s for s in status if s.get('code') == 'succeeded']
        if not len(succeeded):
            self.logger.info(f'No "succeeded" for {project} {package} {repository} {arch}')
            return False

        maxsize = self.maxsize_for_package(package, arch)
        if not maxsize:
            return True

        url = self.api.makeurl(['build', project, repository, arch, package])
        f = self.api.retried_GET(url)
        root = ET.parse(f).getroot()
        for binary in root.findall('binary'):
            if not binary.get('filename', '').endswith('.iso'):
                continue
            isosize = int(binary.get('size', 0))
            if isosize > maxsize:
                self.logger.error('%s %s %s %s: %s' % (
                    project, package, repository, arch, 'too large by %s bytes' % (isosize - maxsize)))
                return False

        return True

    def all_built_products_in_config(self):
        """Verify that all succeeded products are mentioned in the ttm config"""

        # First for all products in product_repo
        products = {}
        for simple_product in self.project.ftp_products + self.project.main_products:
            products[simple_product] = [self.project.product_arch]
        for image_product in self.project.image_products + self.project.container_products:
            products[image_product.package] = image_product.archs

        all_found = self.verify_package_list_complete(self.project.product_repo, products)
        if len(self.project.product_repo_overrides):
            for key, value in self.project.product_repo_overrides.items():
                all_found = self.verify_package_list_complete(value, products)

        # Then for containerfile_products
        if self.project.containerfile_products:
            products = {}
            for image_product in self.project.containerfile_products:
                products[image_product.package] = image_product.archs

            all_found = self.verify_package_list_complete('containerfile', products) and all_found

        return all_found

    def verify_package_list_complete(self, repository, product_archs):
        """Loop through all successfully built products and check whether they
           are part of product_archs (e.g. {'foo:ftp': ['local'], some-image': ['x86_64'], ...})"""

        # Don't return false early, to show all errors at once
        all_found = True

        # Get all results for the product repo from OBS
        url = self.api.makeurl(['build', self.project.name, "_result"],
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
        """Check various conditions required for factory to be snapshotable

        """

        if not self.all_repos_done(self.project.name):
            return False

        all_ok = True

        resultxml = self.api.retried_GET(self.api.makeurl(['build', self.project.name, '_result']))
        prjresult = ET.parse(resultxml).getroot()

        for product in self.project.ftp_products + self.project.main_products:
            if not self.package_ok(prjresult, self.project.name, product,
                                   self.project.product_repo_overrides.get(product, self.project.product_repo),
                                   self.project.product_arch):
                all_ok = False

        # agama-installer in Leap uses images repo as source repo as well as target repo
        source_repo = self.project.product_repo
        if self.project.same_target_images_repo_for_source_repo:
            source_repo = self.project.totest_images_repo
        for product in self.project.image_products + self.project.container_products:
            for arch in product.archs:
                if not self.package_ok(prjresult, self.project.name, product.package, source_repo, arch):
                    all_ok = False

        for product in self.project.containerfile_products:
            for arch in product.archs:
                if not self.package_ok(prjresult, self.project.name, product.package, 'containerfile', arch):
                    all_ok = False

        if len(self.project.livecd_products):
            liveprjname = f'{self.project.name}:Live'
            if not self.all_repos_done(liveprjname):
                return False

            liveresultxml = self.api.retried_GET(self.api.makeurl(['build', liveprjname, '_result']))
            liveprjresult = ET.parse(liveresultxml).getroot()
            for product in self.project.livecd_products:
                for arch in product.archs:
                    if not self.package_ok(liveprjresult, liveprjname, product.package,
                                           self.project.product_repo, arch):
                        all_ok = False

        if not all_ok:
            return False

        # The FTP tree isn't released with setrelease, so it needs to contain
        # the product version already.
        product_version = self.get_product_version()
        if product_version is not None:
            for product in self.project.ftp_products:
                for binary in self.binaries_of_product(self.project.name, product,
                                                       repo=self.project.product_repo_overrides.get(
                                                           product, self.project.product_repo)):
                    # The NonOSS tree doesn't include the version...
                    if binary.endswith('.report') and 'NonOss' not in binary and product_version not in binary:
                        self.logger.debug(f'{binary} in {product} does not include {product_version}')
                        return False

        if self.project.need_same_build_number:
            # make sure all medias have the same build number
            builds = set()
            for p in self.project.ftp_products:
                if 'Addon-NonOss' in p:
                    # XXX: don't care about nonoss atm.
                    continue
                builds.add(self.ftp_build_version(self.project.name, p))
            for p in self.project.main_products:
                builds.add(self.iso_build_version(self.project.name, p))
            for p in self.project.livecd_products + self.project.image_products:
                for arch in p.archs:
                    builds.add(self.iso_build_version(self.project.name, p.package,
                                                      arch=arch))
            if len(builds) != 1:
                self.logger.debug('not all medias have the same build number')
                return False

        return True

    def _release(self, set_release=None):
        for container in self.project.container_products:
            # Containers are built in the same repo as other image products,
            # but released into a different repo in :ToTest
            self.release_package(self.project.name, container.package, repository=self.project.product_repo,
                                 target_project=self.project.test_project,
                                 target_repository=self.project.totest_container_repo)

        for container in self.project.containerfile_products:
            # Dockerfile builds are done in a separate repo, but released into the same location
            # as container_products
            self.release_package(self.project.name, container.package, repository='containerfile',
                                 target_project=self.project.test_project,
                                 target_repository=self.project.totest_container_repo)

        if len(self.project.main_products):
            for product in self.project.ftp_products:
                self.release_package(self.project.name, product,
                                     repository=self.project.product_repo_overrides.get(
                                         product, self.project.product_repo))

            for cd in self.project.main_products:
                self.release_package(self.project.name, cd, set_release=set_release,
                                     repository=self.project.product_repo)

        for cd in self.project.livecd_products:
            self.release_package('%s:Live' %
                                 self.project.name, cd.package, set_release=set_release,
                                 repository=self.project.livecd_repo)

        for image in self.project.image_products:
            source_repo = self.project.product_repo
            if self.project.same_target_images_repo_for_source_repo:
                source_repo = self.project.totest_images_repo
            self.release_package(self.project.name, image.package, set_release=set_release,
                                 repository=source_repo,
                                 target_project=self.project.test_project,
                                 target_repository=self.project.totest_images_repo)

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
            self.api.switch_flag_in_prj(self.project.test_project, flag='publish', state='disable',
                                        repository=self.project.product_repo)

            if self.project.totest_images_repo != self.project.product_repo:
                self.api.switch_flag_in_prj(self.project.test_project, flag='publish', state='disable',
                                            repository=self.project.totest_images_repo)

        self._release(set_release=release)
