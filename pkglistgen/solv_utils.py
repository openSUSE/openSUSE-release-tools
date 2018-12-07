from __future__ import print_function

import logging
import os.path
import random
import string
import sys
import tempfile
import hashlib
import gzip
import io
import subprocess

from lxml import etree as ET

import requests

import solv

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

    if 'update' in baseurl:
        # Could look at .repo file or repomd.xml, but larger change.
        return 'update-' + os.path.basename(os.path.normpath(baseurl)), 'update'

    url = urljoin(baseurl, 'media.1/media')
    with requests.get(url) as media:
        for i, line in enumerate(media.iter_lines()):
            if i != 1:
                continue
            name = line

    if name is not None and '-Build' in name:
        return name, 'media'

    url = urljoin(baseurl, 'media.1/build')
    with requests.get(url) as build:
        name = build.content.strip()

    if name is not None and '-Build' in name:
        return name, 'build'

    raise Exception(baseurl + 'media.1/{media,build} includes no build number')

def dump_solv(baseurl, output_dir, overwrite):
    name = None
    ofh = sys.stdout
    if output_dir:
        build, repo_style = dump_solv_build(baseurl)
        name = os.path.join(output_dir, '{}.solv'.format(build))
        # For update repo name never changes so always update.
        if not overwrite and repo_style != 'update' and os.path.exists(name):
            logger.info("%s exists", name)
            return name

    pool = solv.Pool()
    pool.setarch()

    repo = pool.add_repo(''.join(random.choice(string.letters) for _ in range(5)))
    path_prefix = 'suse/' if name and repo_style == 'build' else ''
    url = urljoin(baseurl, path_prefix + 'repodata/repomd.xml')
    repomd = requests.get(url)
    ns = {'r': 'http://linux.duke.edu/metadata/repo'}
    root = ET.fromstring(repomd.content)
    primary_element = root.find('.//r:data[@type="primary"]', ns)
    location = primary_element.find('r:location', ns).get('href')
    sha256_expected = primary_element.find('r:checksum[@type="sha256"]', ns).text

    # No build information in update repo to use repomd checksum in name.
    if repo_style == 'update':
        name = os.path.join(output_dir, '{}::{}.solv'.format(build, sha256_expected))
        if not overwrite and os.path.exists(name):
            logger.info("%s exists", name)
            return name

        # Only consider latest update repo so remove old versions.
        # Pre-release builds only make sense for non-update repos and once
        # releases then only relevant for next product which does not
        # consider pre-release from previous version.
        for old_solv in glob.glob(os.path.join(output_dir, '{}::*.solv'.format(build))):
            os.remove(old_solv)

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
        if not overwrite and os.path.exists(name) and filecmp.cmp(name + '.new', name, shallow=False):
            logger.debug('file identical, skip dumping')
            os.remove(name + '.new')
        else:
            os.rename(name + '.new', name)
        return name

def solv_merge(solv_merged, *solvs):
    solvs = list(solvs)  # From tuple.

    if os.path.exists(solv_merged):
        modified = map(os.path.getmtime, [solv_merged] + solvs)
        if max(modified) <= modified[0]:
            # The two inputs were modified before or at the same as merged.
            logger.debug('merge skipped for {}'.format(solv_merged))
            return

    with open(solv_merged, 'w') as handle:
        p = subprocess.Popen(['mergesolv'] + solvs, stdout=handle)
        p.communicate()

    if p.returncode:
        raise Exception('failed to create merged solv file')


def solv_cache_update(apiurl, cache_dir_solv, target_project, family_last, family_include):
    """Dump solv files (do_dump_solv) for all products in family."""
    prior = set()

    project_family = project_list_family_prior(
        apiurl, target_project, include_self=True, last=family_last)
    if family_include:
        # Include projects from a different family if desired.
        project_family.extend(project_list_family(apiurl, family_include))

    for project in project_family:
        config = Config(apiurl, project)
        project_config = conf.config[project]

        baseurl = project_config.get('download-baseurl')
        if not baseurl:
            baseurl = project_config.get('download-baseurl-' + project.replace(':', '-'))
        baseurl_update = project_config.get('download-baseurl-update')
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

        # Merge nonfree solv with free solv or copy free solv as merged.
        merged = names[0].replace('.solv', '.merged.solv')
        if len(names) >= 2:
            solv_merge(merged, *names)
        else:
            shutil.copyfile(names[0], merged)
        prior.add(merged)

    return prior
