import logging
import os
import re
import requests
import solv
import subprocess
import tempfile
import glob
from fnmatch import fnmatch
from lxml import etree as ET
from osc.core import http_GET
from osc.core import makeurl

import yaml

from osclib.cache_manager import CacheManager
from osclib.repomirror import RepoMirror

logger = logging.getLogger('InstallChecker')

SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CACHEDIR = CacheManager.directory('repository-meta')


class CorruptRepos(Exception):
    pass

# the content of sp is name, version, release, arch


def _format_pkg(sp):
    return "{}-{}-{}.{}".format(sp[0], sp[1], sp[2], sp[3])


def _check_exists_in_whitelist(sp, whitelist):
    if sp[0] in whitelist:
        logger.debug("Found %s in whitelist, ignoring", sp[0])
        return True
    # check with version
    long_name = "{}-{}".format(sp[0], sp[1])
    if long_name in whitelist:
        logger.debug("Found %s in whitelist, ignoring", long_name)
        return True
    for entry in whitelist:
        if fnmatch(sp[0], entry):
            logger.debug("Found %s matching whitelist entry %s, ignoring", sp[0], entry)
            return True


def _check_colon_format(sp1, sp2, whitelist):
    if "{}:{}".format(sp1, sp2) in whitelist:
        logger.debug("Found %s:%s in whitelist, ignoring", sp1, sp2)
        return True


def _check_conflicts_whitelist(sp1, sp2, whitelist):
    if _check_exists_in_whitelist(sp1, whitelist):
        return True
    if _check_exists_in_whitelist(sp2, whitelist):
        return True
    if _check_colon_format(sp1[0], sp2[0], whitelist):
        return True
    if _check_colon_format(sp2[0], sp1[0], whitelist):
        return True


def _do_packages_conflict(pool, pkgs):
    logger.debug("Checking whether %s can be installed at once", pkgs)
    jobs = []
    for pkg in pkgs:
        sel = pool.select(pkg, solv.Selection.SELECTION_CANON | solv.Selection.SELECTION_DOTARCH)
        if sel.isempty():
            raise RuntimeError(f"{pkg} not found in pool")

        jobs += sel.jobs(solv.Job.SOLVER_INSTALL)

    solver = pool.Solver()
    problems = solver.solve(jobs)
    if not problems:
        return False

    for problem in problems:
        logger.debug("Problem: %s", str(problem))

    return True


def _fileconflicts(pfile, arch, target_packages, whitelist):
    script = os.path.join(SCRIPT_PATH, '..', 'findfileconflicts')
    p = subprocess.run(['perl', script, pfile], stdout=subprocess.PIPE)
    if p.returncode or len(p.stdout):
        output = ''
        conflicts = yaml.safe_load(p.stdout)

        pool = solv.Pool()
        pool.setarch(arch)
        repo = pool.add_repo("packages")
        repo.add_susetags(solv.xfopen(pfile), pool.lookup_id(solv.SOLVID_META, solv.SUSETAGS_DEFAULTVENDOR), "en")
        pool.createwhatprovides()

        for conflict in conflicts:
            sp1 = conflict['between'][0]
            sp2 = conflict['between'][1]

            if sp1[0] not in target_packages and sp2[0] not in target_packages:
                continue

            if _check_conflicts_whitelist(sp1, sp2, whitelist):
                continue

            pkgcanon1 = _format_pkg(sp1)
            pkgcanon2 = _format_pkg(sp2)
            if _do_packages_conflict(pool, [pkgcanon1, pkgcanon2]):
                logger.debug("Packages %s and %s with conflicting files conflict", pkgcanon1, pkgcanon2)
                continue

            output += "found conflict of {} with {}\n".format(_format_pkg(sp1), _format_pkg(sp2))
            for file in conflict['conflicts'].split('\n'):
                output += "  {}\n".format(file)
            output += "\n"

        if len(output):
            return output


def filter_release(line):
    line = re.sub(r'(package [^ ]*\-[^-]*)\-[^-]*(\.\w+) ', r'\1\2 ', line)
    line = re.sub(r'(needed by [^ ]*\-[^-]*)\-[^-]*(\.\w+)$', r'\1\2', line)
    line = re.sub(r'(provided by [^ ]*\-[^-]*)\-[^-]*(\.\w+)$', r'\1\2', line)
    return line


def parsed_installcheck(repos, arch, target_packages, whitelist):
    reported_problems = dict()

    if not len(target_packages):
        return reported_problems

    def maparch2installarch(arch):
        _mapping = {'armv6l': 'armv6hl',
                    'armv7l': 'armv7hl'}
        if arch in _mapping:
            return _mapping[arch]
        return arch

    if not isinstance(repos, list):
        repos = [repos]

    p = subprocess.run(['/usr/bin/installcheck', maparch2installarch(arch)] + repos,
                       stdout=subprocess.PIPE, errors='backslashreplace', text=True)
    if p.returncode:
        in_problem = False
        package = None
        install_re = re.compile(r"^can't install (.*)(-[^-]+-[^-]+):$")
        for line in p.stdout.split('\n'):
            if not line.startswith(' '):
                in_problem = False
            match = install_re.match(line)
            if match:
                package = match.group(1)
                in_problem = False
                if package not in target_packages:
                    continue
                if package in whitelist:
                    logger.debug("{} fails installcheck but is white listed".format(package))
                    continue
                reported_problems[package] = {'problem': match.group(
                    1) + match.group(2), 'output': [], 'source': target_packages[package]}
                in_problem = True
                continue
            if in_problem:
                reported_problems[package]['output'].append(filter_release(line[2:]))

    return reported_problems


def installcheck(directories, arch, whitelist, ignore_conflicts):

    with tempfile.TemporaryDirectory(prefix='repochecker') as dir:
        pfile = os.path.join(dir, 'packages')

        script = os.path.join(SCRIPT_PATH, '..', 'write_repo_susetags_file.pl')
        parts = ['perl', script, dir] + directories

        p = subprocess.run(parts)
        if p.returncode:
            # technically only 126, but there is no other value atm -
            # so if some other perl error happens, we don't continue
            raise CorruptRepos

        target_packages = []
        with open(os.path.join(dir, 'catalog.yml')) as file:
            catalog = yaml.safe_load(file)
            target_packages = catalog.get(directories[0], [])

        parts = []
        output = _fileconflicts(pfile, arch, target_packages, ignore_conflicts)
        if output:
            parts.append(output)

        parsed = parsed_installcheck(pfile, arch, target_packages, whitelist)
        if len(parsed):
            output = ''
            for package in sorted(parsed):
                output += "can't install " + parsed[package]['problem'] + ":\n"
                output += "\n".join(parsed[package]['output'])
                output += "\n\n"
            parts.append(output)

        return parts


def mirrorRepomd(cachedir, url):
    # Use repomd.xml to get the location of primary.xml.*
    repoindex = ET.fromstring(requests.get('{}/repodata/repomd.xml'.format(url)).content)
    primarypath = repoindex.xpath("string(./repo:data[@type='primary']/repo:location/@href)",
                                  namespaces={'repo': 'http://linux.duke.edu/metadata/repo'})

    primarydest = os.path.join(cachedir, os.path.basename(primarypath))
    if not os.path.exists(primarydest):
        # Delete the old files first
        for oldfile in glob.glob(glob.escape(cachedir) + "/*.xml.*"):
            os.unlink(oldfile)

        with tempfile.NamedTemporaryFile(dir=cachedir) as primarytemp:
            primarytemp.write(requests.get(url + '/' + primarypath).content)
            os.link(primarytemp.name, primarydest)
    return primarydest


def mirror(apiurl, project, repository, arch):
    """
    Mirror repo metadata for the given pra.
    Downloads primary.xml for DoD or all RPM headers in the full tree.
    """
    directory = os.path.join(CACHEDIR, project, repository, arch)

    if not os.path.exists(directory):
        os.makedirs(directory)

    meta = ET.parse(http_GET(makeurl(apiurl, ['source', project, '_meta']))).getroot()
    repotag = meta.xpath("/project/repository[@name='{}']".format(repository))[0]
    if arch not in repotag.xpath("./arch/text()"):
        # Arch not in this project, skip mirroring
        return directory

    download = repotag.xpath("./download[@arch='{}']".format(arch))
    if download is not None and len(download) > 0:
        if len(download) > 1:
            raise Exception('Multiple download urls unsupported')
        repotype = download[0].get('repotype')
        if repotype != 'rpmmd':
            raise Exception('repotype {} not supported'.format(repotype))
        return mirrorRepomd(directory, download[0].get('url'))

    rm = RepoMirror(apiurl)
    rm.mirror(directory, project, repository, arch)

    return directory
