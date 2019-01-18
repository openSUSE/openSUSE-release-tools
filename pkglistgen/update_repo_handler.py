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

try:
    from urllib.parse import urljoin
except ImportError:
    # python 2.x
    from urlparse import urljoin

logger = logging.getLogger()

def dump_solv_build(baseurl):
    """Determine repo format and build string from remote repository."""

    buildre = re.compile('.*-Build(.*)')
    url = urljoin(baseurl, 'media.1/media')
    with requests.get(url) as media:
        if media.status_code == requests.codes.ok:
            for i, line in enumerate(media.iter_lines()):
                if i != 1:
                    continue
                build = buildre.match(line)
                if build:
                    return build.group(1)

    url = urljoin(baseurl, 'media.1/build')
    with requests.get(url) as build:
        if build.status_code == requests.codes.ok:
            name = build.content.strip()
            build = buildre.match(name)
            if build:
                return build.group(1)

    url = urljoin(baseurl, 'repodata/repomd.xml')
    with requests.get(url) as media:
        if media.status_code == requests.codes.ok:
            root = ET.parse(url)
            rev = root.find('.//{http://linux.duke.edu/metadata/repo}revision')
            if rev is not None:
                return rev.text

    raise Exception(baseurl + 'includes no build number')

def parse_repomd(repo, baseurl):
    url = urljoin(baseurl, 'repodata/repomd.xml')
    repomd = requests.get(url)
    if repomd.status_code != requests.codes.ok:
        return False

    ns = {'r': 'http://linux.duke.edu/metadata/repo'}
    root = ET.fromstring(repomd.content)
    primary_element = root.find('.//r:data[@type="primary"]', ns)
    location = primary_element.find('r:location', ns).get('href')
    sha256_expected = primary_element.find('r:checksum[@type="sha256"]', ns).text

    f = tempfile.TemporaryFile()
    f.write(repomd.content)
    f.flush()
    os.lseek(f.fileno(), 0, os.SEEK_SET)
    repo.add_repomdxml(f, 0)
    url = urljoin(baseurl, location)
    with requests.get(url, stream=True) as primary:
        if primary.status_code != requests.codes.ok:
            raise Exception(url + ' does not exist')
        sha256 = hashlib.sha256(primary.content).hexdigest()
        if sha256 != sha256_expected:
            raise Exception('checksums do not match {} != {}'.format(sha256, sha256_expected))

        content = gzip.GzipFile(fileobj=io.BytesIO(primary.content))
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        f.write(content.read())
        f.flush()
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        repo.add_rpmmd(f, None, 0)
        return True

    return False

def parse_susetags(repo, baseurl):
    url = urljoin(baseurl, 'content')
    content = requests.get(url)
    if content.status_code != requests.codes.ok:
        return False

    f = tempfile.TemporaryFile()
    f.write(content.content)
    f.flush()
    os.lseek(f.fileno(), 0, os.SEEK_SET)
    repo.add_content(solv.xfopen_fd(None, f.fileno()), 0)

    defvendorid = repo.meta.lookup_id(solv.SUSETAGS_DEFAULTVENDOR)
    descrdir = repo.meta.lookup_str(solv.SUSETAGS_DESCRDIR)
    if not descrdir:
        descrdir = "suse/setup/descr"

    url = urljoin(baseurl, descrdir + '/packages.gz')
    with requests.get(url, stream=True) as packages:
        if packages.status_code != requests.codes.ok:
            raise Exception(url + ' does not exist')

        content = gzip.GzipFile(fileobj=io.BytesIO(packages.content))
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        f.write(content.read())
        f.flush()
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        repo.add_susetags(f, defvendorid, None, solv.Repo.REPO_NO_INTERNALIZE|solv.Repo.SUSETAGS_RECORD_SHARES)
        return True
    return False

def dump_solv(name, baseurl):
    pool = solv.Pool()
    pool.setarch()

    repo = pool.add_repo(''.join(random.choice(string.letters) for _ in range(5)))
    if not parse_repomd(repo, baseurl) and not parse_susetags(repo, baseurl):
        raise Exception('neither repomd nor susetags exists in ' + baseurl)

    repo.create_stubs()

    ofh = open(name, 'w')
    repo.write(ofh)
    ofh.flush()

    return name

def fetch_item(key, opts):
    baseurl = opts['url']
    if not baseurl.endswith('/'):
        baseurl += '/'

    output_dir = '/space/opensuse/home:coolo/00update-repos'
    if opts.get('refresh', False):
        build = dump_solv_build(baseurl)
        name = os.path.join(output_dir, key + '_{}.solv'.format(build))
    else:
        name = os.path.join(output_dir, key + '.solv')

    if os.path.exists(name):
        print(name, 'exists')
        return

    ret = dump_solv(name, baseurl)
    print(key, opts, ret)

def update_project(apiurl, project):
    url = osc.core.makeurl(apiurl, ['source', project, '00update-repos', 'config.yml'])
    root = yaml.safe_load(osc.core.http_GET(url))
    for item in root:
        key = item.keys()[0]
        # cast 15.1 to string :)
        fetch_item(str(key), item[key])
