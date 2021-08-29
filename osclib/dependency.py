import logging
import re

from lxml import etree as ET

import osc.core
import osc.conf
import osclib.core
import osclib.remote_package

class Dependency(object):
    cached_dependencies = {}

    @classmethod
    def compute_rebuilds(cls, source_packages, all_packages, repository=None, archs=None):
        # reset cache to avoid too big caches for different repos and archs
        cls.cached_dependencies = {}

        to_process = source_packages.copy()
        # list of already processed dependencies to avoid loops
        processed = []
        # as result set is fine, as we need all packages that is affected by change in package
        result = set()
        while to_process:
            pkg = to_process.pop()
            result.add(pkg)
            logging.info("Processing %s from %s" % (pkg.name, pkg.source_project_name()))
            for dep in cls.project_dependencies(all_packages, pkg.source_project_name(), repository, archs)[pkg.name]:
                if dep in processed:
                    continue

                to_process.append(dep)

        return result

    @classmethod
    def project_dependencies(cls, all_packages, project_name, target, archs):
        # NOTE: osclib.core.dependson cannot be used as it returns just set and not map
        # and calling it per package is not efficient
        if project_name in cls.cached_dependencies:
            return cls.cached_dependencies[project_name]

        if not archs:
            archs = osclib.core.target_archs(osc.conf.config['apiurl'], project_name, target)

        pkg_mapping = { pkg.name: pkg for pkg in all_packages }
        logging.debug(pkg_mapping)
        res = {}
        for arch in archs:
            raw = osc.core.get_dependson(osc.conf.config['apiurl'], project_name, target, arch, reverse=True)
            tree = ET.fromstring(raw)
            for package in tree:
                name = package.get('name')
                if not name in res:
                    res[name] = set()

                arch_set = set()
                for dep in package.iter('pkgdep'):
                    dep_t = dep.text
                    # remove ignored packages
                    if dep_t in pkg_mapping:
                        arch_set.add(pkg_mapping[dep_t])
                res[name] = res[name].union(arch_set)

        cls.cached_dependencies[project_name] = res
        return res

    def __init__(self, package, dependencies):
        self.package = package
        self.dependencies = dependencies
