import logging
import tempfile
import os
import re
import yaml
import subprocess
from fnmatch import fnmatch
from osclib.cache_manager import CacheManager

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

def _fileconflicts(pfile, target_packages, whitelist):
    script = os.path.join(SCRIPT_PATH, '..', 'findfileconflicts')
    p = subprocess.run(['perl', script, pfile], stdout=subprocess.PIPE)
    if p.returncode or len(p.stdout):
        output = ''
        conflicts = yaml.safe_load(p.stdout)
        for conflict in conflicts:
            sp1 = conflict['between'][0]
            sp2 = conflict['between'][1]

            if not sp1[0] in target_packages and not sp2[0] in target_packages:
                continue

            if _check_conflicts_whitelist(sp1, sp2, whitelist):
                continue

            output += "found conflict of {} with {}\n".format(_format_pkg(sp1), _format_pkg(sp2))
            for file in conflict['conflicts'].split('\n'):
                output += "  {}\n".format(file)
            output += "\n"

        if len(output):
            return output

def _installcheck(pfile, arch, target_packages, whitelist):
    if not len(target_packages):
        return None

    p = subprocess.run(['/usr/bin/installcheck', arch, pfile], stdout=subprocess.PIPE, text=True)
    if p.returncode:
        output = ''
        in_problem = False
        install_re = re.compile(r"^can't install (.*)-[^-]+-[^-]+:$")
        for line in p.stdout.split('\n'):
            if not line.startswith(' '):
                in_problem = False
            match = install_re.match(line)
            if match:
                package = match.group(1)
                in_problem = False
                if not package in target_packages:
                    continue
                if package in whitelist:
                    logger.debug("{} fails installcheck but is white listed".format(package))
                    continue
                in_problem = True
            if in_problem:
                output += line + "\n"
        return output

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
        output = _fileconflicts(pfile, target_packages, ignore_conflicts)
        if output:
            parts.append(output)

        output = _installcheck(pfile, arch, target_packages, whitelist)
        if output:
            parts.append(output)

        return parts

def mirror(apiurl, project, repository, arch):
    """Call bs_mirrorfull script to mirror packages."""
    directory = os.path.join(CACHEDIR, project, repository, arch)
    #return directory

    if not os.path.exists(directory):
        os.makedirs(directory)

    script = os.path.join(SCRIPT_PATH, '..', 'bs_mirrorfull')
    path = '/'.join((project, repository, arch))
    logger.info('mirroring {}'.format(path))
    url = '{}/public/build/{}'.format(apiurl, path)
    p = subprocess.run(['perl', script, '--nodebug', url, directory])

    if p.returncode:
        raise Exception('failed to mirror {}'.format(path))

    return directory
