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

    @staticmethod
    def add_kernel_settings(settings):
        settings = settings.copy()
        if settings['BUILD'].split(":")[-1].startswith('kernel-') and settings['FLAVOR'] == 'Server-DVD-Incidents':
            settings['FLAVOR'] += '-Kernel'
            return [settings]
        return []

    def settings(self, src_prj, dst_prj, packages):
        settings = super(SUSEUpdate, self).settings(src_prj, dst_prj, packages)

        # kGraft Handling - Fully supported kGraft lives in own space, but LTSS in standard LTSS channel
        for x in range(len(settings)):
            if settings[x]['FLAVOR'] == 'Server-DVD-Incidents' and settings[x]['BUILD'].split(
                    ':')[-1].startswith('kgraft-patch'):
                settings[x]['FLAVOR'] = 'Server-DVD-Incidents-Kernel'
                self.logger.warning("kGraft started from INCIDENTS !!")
            if settings[x]['FLAVOR'] == 'Server-DVD-Incidents-Kernel' and not settings[x]['BUILD'].split(
                    ':')[-1].startswith('kgraft-patch'):
                del settings[x]
                continue
            if settings[x]['FLAVOR'] == 'Server-DVD-Incidents-Kernel':
                settings[x]['KGRAFT'] = "1"
                if settings[x]['VERSION'] == '12':
                    settings[x]['real_version'] = self.parse_kgraft_version(self.kgraft_target(self.apiurl, src_prj))

        if not len(settings):
            return []
        settings += self.add_minimal_settings(src_prj, settings[0])
        settings += self.add_kernel_settings(settings[0])
        self.logger.debug("settings are: {}".format(settings))
        return settings
