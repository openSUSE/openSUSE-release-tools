# -*- coding: utf-8 -*-

import re
import requests

import osc.core

from update import Update


MINIMALS = {
    x.rstrip()
    for x in requests.get(
        'https://gitlab.suse.de/qa-maintenance/metadata/raw/master/packages-to-be-tested-on-minimal-systems').iter_lines()
    if len(x) > 0 and not(x.startswith("#") or x.startswith(' '))}


class SUSEUpdate(Update):

    repo_prefix = 'http://download.suse.de/ibs'
    maintenance_project = 'SUSE:Maintenance'

    def __init__(self, settings):
        super(SUSEUpdate, self).__init__(settings)
        self.opensuse = False

    # we take requests that have a kgraft-patch package as kgraft patch (suprise!)
    @staticmethod
    def kgraft_target(apiurl, prj):
        target = None
        skip = False
        pattern = re.compile(r"kgraft-patch-([^.]+)\.")

        for package in osc.core.meta_get_packagelist(apiurl, prj):
            if package.startswith("kernel-"):
                skip = True
                break
            match = re.match(pattern, package)
            if match:
                target = match.group(1)
        if skip:
            return None

        return target

    @staticmethod
    def parse_kgraft_version(kgraft_target):
        return kgraft_target.lstrip('SLE').split('_')[0]

    @staticmethod
    def kernel_target(req):
        if req:
            for a in req.actions:
                # kernel incidents have kernel-source package (suprise!)
                if a.src_package.startswith('kernel-source'):
                    return True, a
        return None, None

    def add_minimal_settings(self, prj, settings):
        minimal = False
        for pkg in self.incident_packages(prj):
            if pkg in MINIMALS:
                minimal = True
        if not minimal:
            return []

        settings = settings.copy()
        settings['FLAVOR'] += '-Minimal'
        return [settings]

    def settings(self, src_prj, dst_prj, packages):
        settings = super(SUSEUpdate, self).settings(src_prj, dst_prj, packages)
        if not len(settings):
            return []

        settings += self.add_minimal_settings(src_prj, settings[0])

        return settings
