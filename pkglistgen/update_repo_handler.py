from __future__ import print_function

import filecmp
import glob
import gzip
import hashlib
import io
import logging
import os.path
import re
import random
import string
import subprocess
import sys
import shutil
import tempfile

from lxml import etree as ET

from osc import conf
import osc.core
from osclib.util import project_list_family
from osclib.util import project_list_family_prior
from osclib.conf import Config
from osclib.cache_manager import CacheManager

import requests

import solv

import yaml

# share header cache with repochecker
CACHEDIR = CacheManager.directory('repository-meta')

try:
    from urllib.parse import urljoin
except ImportError:
    # python 2.x
    from urlparse import urljoin

logger = logging.getLogger()

def dump_solv_build(baseurl):
    """Determine repo format and build string from remote repository."""

    if not baseurl.endswith('/'):
        baseurl += '/'

    buildre = re.compile('.*-Build(.*)')
    url = urljoin(baseurl, 'media.1/media')
    with requests.get(url) as media:
        for i, line in enumerate(media.iter_lines()):
            if i != 1:
                continue
            build = buildre.match(line)
            if build:
                return build.group(1)

    url = urljoin(baseurl, 'media.1/build')
    with requests.get(url) as build:
        name = build.content.strip()
        build = buildre.match(name)
        if build:
            return build.group(1)

    url = urljoin(baseurl, 'repodata/repomd.xml')
    with requests.get(url) as media:
        root = ET.parse(url)
        rev = root.find('.//{http://linux.duke.edu/metadata/repo}revision')
        if rev is not None:
            return rev.text

    raise Exception(baseurl + 'includes no build number')

def dump_solv(baseurl, output_dir):
    name = None
    ofh = sys.stdout
    if output_dir:
        build = dump_solv_build(baseurl)
        name = os.path.join(output_dir, '{}.solv'.format(build))

    pool = solv.Pool()
    pool.setarch()

    repo = pool.add_repo(''.join(random.choice(string.letters) for _ in range(5)))
    url = urljoin(baseurl, 'repodata/repomd.xml')
    repomd = requests.get(url)
    ns = {'r': 'http://linux.duke.edu/metadata/repo'}
    root = ET.fromstring(repomd.content)
    print(url, root)
    primary_element = root.find('.//r:data[@type="primary"]', ns)
    location = primary_element.find('r:location', ns).get('href')
    sha256_expected = primary_element.find('r:checksum[@type="sha256"]', ns).text

    path_prefix = 'TODO'
    f = tempfile.TemporaryFile()
    f.write(repomd.content)
    f.flush()
    os.lseek(f.fileno(), 0, os.SEEK_SET)
    repo.add_repomdxml(f, 0)
    url = urljoin(baseurl, path_prefix + location)
    with requests.get(url, stream=True) as primary:
        sha256 = hashlib.sha256(primary.content).hexdigest()
        if sha256 != sha256_expected:
            raise Exception('checksums do not match {} != {}'.format(sha256, sha256_expected))

        content = gzip.GzipFile(fileobj=io.BytesIO(primary.content))
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        f.write(content.read())
        f.flush()
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        repo.add_rpmmd(f, None, 0)
        repo.create_stubs()

        ofh = open(name + '.new', 'w')
        repo.write(ofh)

    if name is not None:
        # Only update file if overwrite or different.
        ofh.flush()  # Ensure entirely written before comparing.
        os.rename(name + '.new', name)
        return name

def solv_cache_update(apiurl, cache_dir_solv, target_project, family_last, family_include):
    """Dump solv files (do_dump_solv) for all products in family."""
    prior = set()

    project_family = project_list_family_prior(
        apiurl, target_project, include_self=True, last=family_last)
    if family_include:
        # Include projects from a different family if desired.
        project_family.extend(project_list_family(apiurl, family_include))

    for project in project_family:
        Config(apiurl, project)
        project_config = conf.config[project]

        baseurl = project_config.get('download-baseurl')
        if not baseurl:
            baseurl = project_config.get('download-baseurl-' + project.replace(':', '-'))
        baseurl_update = project_config.get('download-baseurl-update')
        print(project, baseurl, baseurl_update)
        continue

        if not baseurl:
            logger.warning('no baseurl configured for {}'.format(project))
            continue

        urls = [urljoin(baseurl, 'repo/oss/')]
        if baseurl_update:
            urls.append(urljoin(baseurl_update, 'oss/'))
        if project_config.get('nonfree'):
            urls.append(urljoin(baseurl, 'repo/non-oss/'))
            if baseurl_update:
                urls.append(urljoin(baseurl_update, 'non-oss/'))

        names = []
        for url in urls:
            project_display = project
            if 'update' in url:
                project_display += ':Update'
            print('-> dump_solv for {}/{}'.format(
                project_display, os.path.basename(os.path.normpath(url))))
            logger.debug(url)

            output_dir = os.path.join(cache_dir_solv, project)
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            solv_name = dump_solv(baseurl=url, output_dir=output_dir, overwrite=False)
            if solv_name:
                names.append(solv_name)

        if not len(names):
            logger.warning('no solv files were dumped for {}'.format(project))
            continue

    print(prior)
    return prior


def update_merge(nonfree, repos, architectures):
    """Merge free and nonfree solv files or copy free to merged"""
    for project, repo in repos:
        for arch in architectures:
            solv_file = os.path.join(
                CACHEDIR, 'repo-{}-{}-{}.solv'.format(project, repo, arch))
            solv_file_merged = os.path.join(
                CACHEDIR, 'repo-{}-{}-{}.merged.solv'.format(project, repo, arch))

            if not nonfree:
                shutil.copyfile(solv_file, solv_file_merged)
                continue

            solv_file_nonfree = os.path.join(
                CACHEDIR, 'repo-{}-{}-{}.solv'.format(nonfree, repo, arch))

def fetch_item(key, opts):
    ret = dump_solv(opts['url'], '/tmp')
    print(key, opts, ret)

def update_project(apiurl, project):
    url = osc.core.makeurl(apiurl, ['source', project, '00update-repos', 'config.yml'])
    root = yaml.safe_load(osc.core.http_GET(url))
    for item in root:
        key = item.keys()[0]
        fetch_item(key, item[key])
