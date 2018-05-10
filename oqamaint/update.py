# -*- coding: utf-8 -*-

import re
import requests


# python 3 has gzip decompress function
try:
    from gzip import decompress
except ImportError:
    from gzip import GzipFile
    import io

    def decompress(data):
        with GzipFile(fileobj=io.BytesIO(data)) as f:
            return f.read()

# use cElementTree by default, fallback to pure python
try:
    from xml.etree import cElementTree as ET
except ImportError:
    from xml.etree import ElementTree as ET

import osc.core

from osclib.memoize import memoize


class Update(object):
    incident_name_cache = {}

    def __init__(self, settings):
        self._settings = settings
        self._settings['_NOOBSOLETEBUILD'] = '1'
        self.opensuse = True

    def get_max_revision(self, job):
        repo = self.repo_prefix + '/'
        repo += self.maintenance_project.replace(':', ':/')
        repo += ':/{!s}'.format(job['id'])
        max_revision = 0
        for channel in job['channels']:
            crepo = repo + '/' + channel.replace(':', '_')
            xml = requests.get(crepo + '/repodata/repomd.xml')
            if not xml.ok:
                self.logger.info("{} skipped .. need wait".format(crepo))
                # if one fails, we skip it and wait
                return False
            root = ET.fromstring(xml.text)
            rev = root.find('.//{http://linux.duke.edu/metadata/repo}revision')
            rev = int(rev.text)
            if rev > max_revision:
                max_revision = rev
        return max_revision

    def settings(self, src_prj, dst_prj):
        s = self._settings.copy()

        build = src_prj.split(':')[-1]
        # start with a colon so it looks cool behind 'Build' :/
        s['BUILD'] = ':' + build
        name = self.incident_name(src_prj)
        repo = dst_prj.replace(':', '_')
        repo = '{!s}/{!s}/{!s}/'.format(self.repo_prefix, src_prj.replace(':', ':/'), repo)
        patch_id = self.patch_id(repo)
        if not patch_id and self.opensuse:
            # hot fix for openSUSE
            patch_id = build
        elif not patch_id and not self.opensuse:
            s['skip_job'] = 1
        s['INCIDENT_REPO'] = repo
        s['INCIDENT_PATCH'] = patch_id
        s['BUILD'] += ':' + name
        return [s]

    @memoize()
    def incident_packages(self, prj):
        packages = []
        for package in osc.core.meta_get_packagelist(self.apiurl, prj):
            if package.endswith('SUSE_Channels') or package.startswith('patchinfo'):
                continue
            parts = package.split('.')
            # remove target name
            parts.pop()
            packages.append('.'.join(parts))
        return packages

    # grab the updateinfo from the given repo and return its patch's id
    @staticmethod
    def patch_id(repo):
        url = repo + 'repodata/repomd.xml'
        repomd = requests.get(url)
        if not repomd.ok:
            return None
        root = ET.fromstring(repomd.text)

        cs = root.find(
            './/{http://linux.duke.edu/metadata/repo}data[@type="updateinfo"]/{http://linux.duke.edu/metadata/repo}location')
        try:
            url = repo + cs.attrib['href']
        except AttributeError:
            return None

        repomd = requests.get(url).content
        root = ET.fromstring(decompress(repomd))
        return root.find('.//id').text

    # take the first package name we find - often enough correct
    def incident_name(self, prj):
        if prj not in self.incident_name_cache:
            self.incident_name_cache[prj] = self._incident_name(prj)
        return self.incident_name_cache[prj]

    def _incident_name(self, prj):
        shortest_pkg = None
        for package in osc.core.meta_get_packagelist(self.apiurl, prj):
            if package.startswith('patchinfo'):
                continue
            if package.endswith('SUSE_Channels'):
                continue
            # other tools on SLE have data from SMELT without access to this attrib
            if self.opensuse:
                url = osc.core.makeurl(self.apiurl, ('source', prj, package, '_link'))
                root = ET.parse(osc.core.http_GET(url)).getroot()
                if root.attrib.get('cicount'):
                    continue
                # super hack, but we need to strip the suffix from the package name
                # but bash.openSUSE_Leap_42.3_Update doesn't leave many options
                # without reverse engineering OBS :(
                package = re.sub(r'\.openSUSE_Leap_.*$', '.openSUSE', package)
            if not shortest_pkg or len(package) < len(shortest_pkg):
                shortest_pkg = package
        if not shortest_pkg:
            shortest_pkg = 'unknown'
        match = re.match(r'^(.*)\.[^\.]*$', shortest_pkg)

        return match.group(1) if match else shortest_pkg
