#!/usr/bin/python3

import datetime
import difflib
import hashlib
import logging
import os
import os.path
import re
import subprocess
import sys
import tempfile
import cmdln
from urllib.parse import urlencode

import yaml
from lxml import etree as ET
from osc import conf

import ToolBase
from osclib.conf import Config
from osclib.core import (http_GET, http_POST, makeurl,
                         project_pseudometa_file_ensure,
                         repository_path_expand, repository_path_search,
                         target_archs, source_file_load, source_file_ensure)
from osclib.repochecks import installcheck, mirror, parsed_installcheck, CorruptRepos

class RepoChecker():
    def __init__(self):
        self.logger = logging.getLogger('RepoChecker')
        self.store_project = None
        self.store_package = None
        self.rebuild = None

    def parse_store(self, project_package):
        if project_package:
            self.store_project, self.store_package = project_package.split('/')

    def check(self, project, repository):
        if not repository:
            repository = self.project_repository(project)
        if not repository:
            self.logger.error('a repository must be specified via OSRT:Config main-repo for {}'.format(project))
            return

        config = Config.get(self.apiurl, project)

        archs = target_archs(self.apiurl, project, repository)
        if not len(archs):
            self.logger.debug('{} has no relevant architectures'.format(project))
            return None

        for arch in archs:
            self.check_pra(project, repository, arch)

    def project_pseudometa_file_name(self, project, repository):
        filename = 'repo_checker'

        main_repo = Config.get(self.apiurl, project).get('main-repo')
        if not main_repo:
            filename += '.' + repository

        return filename

    def _split_and_filter(self, output):
        output = output.split("\n")
        for lnr, line in enumerate(output):
            if line.startswith('FOLLOWUP'):
                # there can be multiple lines with missing providers
                while lnr >= 0 and output[lnr - 1].endswith('none of the providers can be installed'):
                    output.pop()
                    lnr = lnr - 1
        for lnr in reversed(range(len(output))):
            # those lines are hardly interesting for us
            if output[lnr].find('(we have') >= 0:
                del output[lnr]
            else:
                output[lnr] = output[lnr]
        return output

    def project_repository(self, project):
        repository = Config.get(self.apiurl, project).get('main-repo')
        if not repository:
            self.logger.debug('no main-repo defined for {}'.format(project))

            search_project = 'openSUSE:Factory'
            for search_repository in ('snapshot', 'standard'):
                repository = repository_path_search(
                    self.apiurl, project, search_project, search_repository)

                if repository:
                    self.logger.debug('found chain to {}/{} via {}'.format(
                        search_project, search_repository, repository))
                    break

        return repository

    def store_yaml(self, state, project, repository, arch):
        state_yaml = yaml.dump(state, default_flow_style=False)
        comment = 'Updated rebuild infos for {}/{}/{}'.format(project, repository, arch)
        source_file_ensure(self.apiurl, self.store_project, self.store_package,
                           self.store_filename, state_yaml, comment=comment)

    def check_pra(self, project, repository, arch):
        config = Config.get(self.apiurl, project)

        oldstate = None
        self.store_filename = 'rebuildpacs.{}-{}.yaml'.format(project, repository)
        state_yaml = source_file_load(self.apiurl, self.store_project, self.store_package,
                                      self.store_filename)
        if state_yaml:
            oldstate = yaml.safe_load(state_yaml)

        oldstate = oldstate or {}
        oldstate.setdefault('check', {})
        oldstate.setdefault('leafs', {})

        repository_pairs = repository_path_expand(self.apiurl, project, repository)
        directories = []
        for pair_project, pair_repository in repository_pairs:
            directories.append(mirror(self.apiurl, pair_project, pair_repository, arch))

        parsed = dict()
        with tempfile.TemporaryDirectory(prefix='repochecker') as dir:
            pfile = os.path.join(dir, 'packages')

            SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
            script = os.path.join(SCRIPT_PATH, 'write_repo_susetags_file.pl')
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

            parsed = parsed_installcheck(pfile, arch, target_packages, [])
            for package in parsed:
                parsed[package]['output'] = "\n".join(parsed[package]['output'])

            # let's risk a N*N algorithm in the hope that we have a limited N
            for package1 in parsed:
                output = parsed[package1]['output']
                for package2 in parsed:
                    if package1 == package2:
                        continue
                    output = output.replace(parsed[package2]['output'], 'FOLLOWUP(' + package2 + ')')
                parsed[package1]['output'] = output

            for package in parsed:
                parsed[package]['output'] = self._split_and_filter(parsed[package]['output'])

        url = makeurl(self.apiurl, ['build', project, '_result'], {
                      'repository': repository, 'arch': arch, 'code': 'succeeded'})
        root = ET.parse(http_GET(url)).getroot()
        succeeding = list(map(lambda x: x.get('package'), root.findall('.//status')))

        per_source = dict()

        for package, entry in parsed.items():
            source = "{}/{}/{}/{}".format(project, repository, arch, entry['source'])
            per_source.setdefault(source, {'output': [], 'builds': entry['source'] in succeeding})
            per_source[source]['output'].extend(entry['output'])

        rebuilds = set()

        for source in sorted(per_source):
            if not len(per_source[source]['output']):
                continue
            self.logger.debug("{} builds: {}".format(source, per_source[source]['builds']))
            self.logger.debug("  " + "\n  ".join(per_source[source]['output']))
            if not per_source[source]['builds']:  # nothing we can do
                continue
            old_output = oldstate['check'].get(source, {}).get('problem', [])
            if sorted(old_output) == sorted(per_source[source]['output']):
                self.logger.debug("unchanged problem")
                continue
            self.logger.info("rebuild %s", source)
            rebuilds.add(os.path.basename(source))
            for line in difflib.unified_diff(old_output, per_source[source]['output'], 'before', 'now'):
                self.logger.debug(line.strip())
            oldstate['check'][source] = {'problem': per_source[source]['output'],
                                         'rebuild':  str(datetime.datetime.now())}

        for source in list(oldstate['check']):
            if not source.startswith('{}/{}/{}/'.format(project, repository, arch)):
                continue
            if not os.path.basename(source) in succeeding:
                continue
            if source not in per_source:
                self.logger.info("No known problem, erasing %s", source)
                del oldstate['check'][source]

        packages = config.get('rebuildpacs-leafs', '').split()
        if not self.rebuild: # ignore in this case
            packages = []

        # first round: collect all infos from obs
        infos = dict()
        for package in packages:
            subpacks, build_deps = self.check_leaf_package(project, repository, arch, package)
            infos[package] = {'subpacks': subpacks, 'deps': build_deps}

        # calculate rebuild triggers
        rebuild_triggers = dict()
        for package1 in packages:
            for package2 in packages:
                if package1 == package2:
                    continue
                for subpack in infos[package1]['subpacks']:
                    if subpack in infos[package2]['deps']:
                        rebuild_triggers.setdefault(package1, set())
                        rebuild_triggers[package1].add(package2)
                        # ignore this depencency. we already trigger both of them
                        del infos[package2]['deps'][subpack]

        # calculate build info hashes
        for package in packages:
            if not package in succeeding:
                self.logger.debug("Ignore %s for the moment, not succeeding", package)
                continue
            m = hashlib.sha256()
            for bdep in sorted(infos[package]['deps']):
                m.update(bytes(bdep + '-' + infos[package]['deps'][bdep], 'utf-8'))
            state_key = '{}/{}/{}/{}'.format(project, repository, arch, package)
            olddigest = oldstate['leafs'].get(state_key, {}).get('buildinfo')
            if olddigest == m.hexdigest():
                continue
            self.logger.info("rebuild leaf package %s (%s vs %s)", package, olddigest, m.hexdigest())
            rebuilds.add(package)
            oldstate['leafs'][state_key] = {'buildinfo': m.hexdigest(),
                                            'rebuild': str(datetime.datetime.now())}

        if self.dryrun:
            if self.rebuild:
                self.logger.info("To rebuild: %s", ' '.join(rebuilds))
            return

        if not self.rebuild or not len(rebuilds):
            self.logger.debug("Nothing to rebuild")
            # in case we do rebuild, wait for it to succeed before saving
            self.store_yaml(oldstate, project, repository, arch)
            return

        query = {'cmd': 'rebuild', 'repository': repository, 'arch': arch, 'package': rebuilds}
        url = makeurl(self.apiurl, ['build', project], urlencode(query, doseq=True))
        http_POST(url)

        self.store_yaml(oldstate, project, repository, arch)

    def check_leaf_package(self, project, repository, arch, package):
        url = makeurl(self.apiurl, ['build', project, repository, arch, package, '_buildinfo'])
        root = ET.parse(http_GET(url)).getroot()
        subpacks = set()
        for sp in root.findall('subpack'):
            subpacks.add(sp.text)
        build_deps = dict()
        for bd in root.findall('bdep'):
            if bd.get('notmeta') == '1':
                continue
            build_deps[bd.get('name')] = bd.get('version') + '-' + bd.get('release')
        return subpacks, build_deps


class CommandLineInterface(ToolBase.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def setup_tool(self):
        return RepoChecker()

    @cmdln.option('--store', help='Project/Package to store the rebuild infos in')
    @cmdln.option('-r', '--repo', dest='repo', help='Repository to check')
    @cmdln.option('--no-rebuild', dest='norebuild', action='store_true', help='Only track issues, do not rebuild')
    def do_check(self, subcmd, opts, project):
        """${cmd_name}: Rebuild packages in rebuild=local projects

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.tool.rebuild = not opts.norebuild
        self.tool.parse_store(opts.store)
        self.tool.apiurl = conf.config['apiurl']
        self.tool.check(project, opts.repo)

if __name__ == '__main__':
    app = CommandLineInterface()
    sys.exit(app.main())
