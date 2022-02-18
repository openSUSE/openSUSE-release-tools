
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
from urllib.error import HTTPError
from osclib.cache_manager import CacheManager

import requests

import solv

import yaml

from urllib.parse import urljoin, urlparse

logger = logging.getLogger()

def dump_solv_build(baseurl):
    """Determine repo format and build string from remote repository."""

    buildre = re.compile(r'.*-Build(.*)')
    factoryre = re.compile(r'openSUSE-(\d*)-.*-Build.*')
    url = urljoin(baseurl, 'media.1/media')
    with requests.get(url) as media:
        media.encoding = 'utf-8'
        if media.status_code == requests.codes.ok:
            for i, line in enumerate(media.iter_lines(decode_unicode=True)):
                if i != 1:
                    continue
                build = factoryre.match(line)
                if build:
                    return build.group(1)
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
    repo.add_repomdxml(solv.xfopen_fd(None, f.fileno()), 0)
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
        repo.add_rpmmd(solv.xfopen_fd(None, f.fileno()), None, 0)
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
        descrdir = 'suse/setup/descr'

    url = urljoin(baseurl, descrdir + '/packages.gz')
    with requests.get(url, stream=True) as packages:
        if packages.status_code != requests.codes.ok:
            raise Exception(url + ' does not exist')

        content = gzip.GzipFile(fileobj=io.BytesIO(packages.content))
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        f.write(content.read())
        f.flush()
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        try:
            repo.add_susetags(f, defvendorid, None, solv.Repo.REPO_NO_INTERNALIZE | solv.Repo.SUSETAGS_RECORD_SHARES)
        except TypeError:
            logger.error(f"Failed to add susetags for {url}")
            return False
        return True
    return False

def dump_solv(name, baseurl):
    pool = solv.Pool()
    pool.setarch()

    repo = pool.add_repo(''.join(random.choice(string.ascii_letters) for _ in range(5)))
    if not parse_repomd(repo, baseurl) and not parse_susetags(repo, baseurl):
        raise Exception('neither repomd nor susetags exists in ' + baseurl)

    repo.create_stubs()

    ofh = solv.xfopen(name, 'w')
    repo.write(ofh)
    ofh.flush()

    return name

def print_repo_delta(pool, repo2, packages_file):
    print('=Ver: 2.0', file=packages_file)
    present = dict()
    for s in pool.solvables_iter():
        if s.repo != repo2:
            key = '{}/{}'.format(s.name, s.arch)
            present.setdefault(key, {})
            present[key][s.evr] = s.repo
    for s in repo2.solvables:
        if s.arch == 'src':
            continue
        key = '{}/{}'.format(s.name, s.arch)
        if present.get(key, {}).get(s.evr):
            continue
        elif key not in present:
            print('# NEW', s.name, s.arch, file=packages_file)
        evr = s.evr.split('-')
        release = evr.pop()
        print('=Pkg:', s.name, '-'.join(evr), release, s.arch, file=packages_file)
        print('+Prv:', file=packages_file)
        for dep in s.lookup_deparray(solv.SOLVABLE_PROVIDES):
            print(dep, file=packages_file)
        print('-Prv:', file=packages_file)

def merge_susetags(output, files):
    pool = solv.Pool()
    pool.setarch()

    for file in files:
        oldsysrepo = pool.add_repo(file)
        defvendorid = oldsysrepo.meta.lookup_id(solv.SUSETAGS_DEFAULTVENDOR)
        f = tempfile.TemporaryFile()
        st = subprocess.call(['xz', '-cd', file], stdout=f.fileno())
        os.lseek(f.fileno(), 0, os.SEEK_SET)
        oldsysrepo.add_susetags(solv.xfopen_fd(None, f.fileno()), defvendorid, None, solv.Repo.REPO_NO_INTERNALIZE | solv.Repo.SUSETAGS_RECORD_SHARES)

    packages = dict()
    for s in pool.solvables_iter():
        evr = s.evr.split('-')
        release = evr.pop()
        version = '-'.join(evr)
        key = s.name + "-" + version + "." + s.arch
        if re.search('-release', s.name):  # just take one version of it
            key = s.name + "." + s.arch
        packages[key] = { 'name': s.name, 'version': version, 'arch': s.arch, 'release': release, 'provides': set()}
        for dep in s.lookup_deparray(solv.SOLVABLE_PROVIDES):
            packages[key]['provides'].add(str(dep))
    output_file = open(output, 'w')
    print("=Ver: 2.0", file=output_file)
    for package in sorted(packages):
        infos = packages[package]
        print('=Pkg:', infos['name'], infos['version'], infos['release'], infos['arch'], file=output_file)
        print('+Prv:', file=output_file)
        for dep in sorted(infos['provides']):
            print(dep, file=output_file)
        print('-Prv:', file=output_file)


def update_project(apiurl, project):
    # Cache dir specific to hostname and project.
    host = urlparse(apiurl).hostname
    cache_dir = CacheManager.directory('update_repo_handler', host, project)
    repo_dir = os.path.join(cache_dir, '000update-repos')

    # development aid
    checkout = True
    if checkout:
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
        os.makedirs(cache_dir)

        osc.core.checkout_package(apiurl, project, '000update-repos', expand_link=True, prj_dir=cache_dir)

    package = osc.core.Package(repo_dir)

    root = yaml.safe_load(open(os.path.join(repo_dir, 'config.yml')))
    for item in root:
        key = list(item)[0]
        opts = item[key]
        # cast 15.1 to string :)
        key = str(key)
        if not opts['url'].endswith('/'):
            opts['url'] += '/'

        if opts.get('refresh', False):
            opts['build'] = dump_solv_build(opts['url'])
            path = '{}_{}.packages'.format(key, opts['build'])
        else:
            path = key + '.packages'
        packages_file = os.path.join(repo_dir, path)

        if opts.get('refresh', False):
            oldfiles = glob.glob(os.path.join(repo_dir, '{}_*.packages.xz'.format(key)))
            if len(oldfiles) > 10:
                oldest = oldfiles[-1]
                if oldest.count('and_before') > 1:
                    raise Exception('The oldest is already a compated file')
                oldest = oldest.replace('.packages.xz', '_and_before.packages')
                merge_susetags(oldest, oldfiles)
                for file in oldfiles:
                    os.unlink(file)
                    package.delete_file(os.path.basename(file))
                subprocess.check_call(['xz', oldest])
                package.addfile(os.path.basename(oldest) + ".xz")

        if os.path.exists(packages_file + '.xz'):
            print(path, 'already exists')
            continue

        solv_file = packages_file + '.solv'
        dump_solv(solv_file, opts['url'])

        pool = solv.Pool()
        pool.setarch()

        if opts.get('refresh', False):
            for file in glob.glob(os.path.join(repo_dir, '{}_*.packages.xz'.format(key))):
                repo = pool.add_repo(file)
                defvendorid = repo.meta.lookup_id(solv.SUSETAGS_DEFAULTVENDOR)
                f = tempfile.TemporaryFile()
                # FIXME: port to lzma module with python3
                st = subprocess.call(['xz', '-cd', file], stdout=f.fileno())
                os.lseek(f.fileno(), 0, os.SEEK_SET)
                repo.add_susetags(solv.xfopen_fd(None, f.fileno()), defvendorid, None, solv.Repo.REPO_NO_INTERNALIZE | solv.Repo.SUSETAGS_RECORD_SHARES)

        repo1 = pool.add_repo(''.join(random.choice(string.ascii_letters) for _ in range(5)))
        repo1.add_solv(solv_file)

        print_repo_delta(pool, repo1, open(packages_file, 'w'))
        subprocess.call(['xz', '-9', packages_file])
        os.unlink(solv_file)

        package.addfile(os.path.basename(path + '.xz'))
        del pool

    package.commit('Automatic update')
