#!/usr/bin/python3

# SPDX-License-Identifier: MIT

import json
import os
import shutil
import sys
import re
from pathlib import Path
from typing import Set

from git.config import GitConfigParser
from urllib.error import HTTPError

import osc.conf
import osc.core
import ReviewBot

http_GET = osc.core.http_GET
MAINTAINERSHIP_FILE = "_maintainership.json"
WHITELIST_FILE = "whitelist_maintainership.json"


class CheckerBugowner(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.request_default_return = True
        self.override_allow = False

    def _obs_check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        self.logger.info("%s/%s@%s -> %s/%s" % (src_project,
                                                src_package, src_rev, target_project, target_package))
        if src_package.startswith('patchinfo'):
            return True
        if self.exists_in(target_project, target_package):
            return True
        for line in self.request.description.splitlines():
            matched_package = None
            matched_maintainer = None
            m = re.match(r'\s*bugowner:\s*(\S+)\s*$', line)
            if m:
                matched_maintainer = m.group(1)
            m = re.match(r'\s*bugowner:\s(\S+)\s(\S+)\s*$', line)
            if m:
                matched_maintainer = m.group(2)
                matched_package = m.group(1)
            if not matched_maintainer:
                continue
            if matched_package and matched_package != target_package:
                continue
            if not self.valid_maintainer(matched_maintainer):
                self.review_messages['declined'] += f"\n{matched_maintainer} could not be found on this instance."
                return False
            return True
        self.review_messages['declined'] += f"\n{target_package} appears to be a new package and " + \
            "no matching 'bugowner:' line could be found in the request description. See https://confluence.suse.com/x/WgH2OQ"
        return False

    def existing_url(self, url):
        "Return False if url returns 404"
        try:
            osc.core.http_GET(url)
        except HTTPError as e:
            if e.code == 404:
                return False
        return True

    def valid_maintainer(self, maintainer):
        if maintainer.startswith('group:'):
            maintainer = maintainer.replace('group:', '')
            url = osc.core.makeurl(self.apiurl, ['group', maintainer])
            return self.existing_url(url)
        url = osc.core.makeurl(self.apiurl, ['person', maintainer])
        return self.existing_url(url)

    def exists_in(self, project, package):
        url = osc.core.makeurl(self.apiurl, ['source', project, package])
        return self.existing_url(url)

    @staticmethod
    def get_request_from_src_rev(requests, src_rev):
        for request in requests:
            if request.actions[0].src_rev == src_rev:
                return request

        return None

    def _gitea_checkout(self, project: str, package: str, revision: str):
        local_dir = Path(
            os.path.expanduser(
                f"~/.cache/bugowner_checker_git/{project}_{package}_{revision}"
            )
        )
        if local_dir.is_dir():
            self.logger.warning(f"directory {local_dir} already exists, removing it.")
            shutil.rmtree(local_dir, ignore_errors=True)

        local_dir.mkdir(parents=True)

        self.scm.checkout_package(
            target_project=project,
            target_package=package,
            pathname=local_dir,
            revision=revision,
        )

        return Path(local_dir, package)

    def _diff_submodules(self, head_gitmodules: Path, base_gitmodules: Path):
        head_config = GitConfigParser(file_or_files=head_gitmodules, read_only=True)
        base_config = GitConfigParser(file_or_files=base_gitmodules, read_only=True)

        head_sections = head_config.sections()
        base_sections = base_config.sections()

        self.logger.debug(f"HEAD submodule sections: {head_sections}")
        self.logger.debug(f"base submodule sections: {base_sections}")

        head_submodules = {
            section for section in head_sections if section.startswith("submodule ")
        }
        base_submodules = {
            section for section in base_sections if section.startswith("submodule ")
        }

        new_submodule_sections = head_submodules - base_submodules

        new_submodules = set()
        if new_submodule_sections:
            for section in new_submodule_sections:
                try:
                    # Extract name from 'submodule "name"'
                    name = section.split('"')[1]
                    new_submodules.add(name)
                    self.logger.info(f"Found new submodule: {name}")
                except IndexError:
                    self.logger.error(f"Could not parse submodule section: {section}")
        else:
            self.logger.info("No new submodules found.")

        return new_submodules

    def _load_whitelist_data(self, file: Path) -> Set[str]:
        try:
            with open(file) as f:
                # Assuming it's a JSON array (list) of strings
                data = json.load(f)

                if not isinstance(data, list):
                    raise ValueError(f"Whitelist file '{WHITELIST_FILE}' must contain a JSON array/list, but {type(data)} was found.")

                # Convert list of package names to a set for fast lookups
                return set(data)

        except FileNotFoundError:
            self.logger.warning(f"Whitelist file '{WHITELIST_FILE}' not found. Skipping the whitelist check.")
            return set()

    def _load_maintainership_data(self, file: Path) -> Set[str]:
        with open(file) as f:
            # Assuming it's a JSON dictionary
            data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError(f"Maintainership file '{MAINTAINERSHIP_FILE}' must contain a JSON dict, but {type(data)} was found.")

            # Convert list of package names to a set for fast lookups
            return set(data.keys())

    def _gitea_validate(
        self,
        head_project: str,
        head_package: str,
        head_revision: str,
        base_project: str,
        base_package: str,
        base_revision: str,
    ) -> bool:
        head_dir = self._gitea_checkout(head_project, head_package, head_revision)
        base_dir = self._gitea_checkout(base_project, base_package, base_revision)

        new_submodules = self._diff_submodules(
            Path(head_dir, ".gitmodules"), Path(base_dir, ".gitmodules")
        )

        maintained = self._load_maintainership_data(Path(head_dir, MAINTAINERSHIP_FILE))
        whitelisted = self._load_whitelist_data(Path(head_dir, WHITELIST_FILE))

        orphan_packages = set()
        for package in new_submodules:
            if package not in maintained and package not in whitelisted:
                orphan_packages.add(package)

        return orphan_packages

    def _gitea_check_source_submission(
        self,
        head_project: str,
        head_package: str,
        head_revision: str,
        base_project: str,
        base_package: str,
    ) -> bool:

        request = self.get_request_from_src_rev(self.requests, head_revision)
        if not request:
            self.logger.warning(f"Request with HEAD {head_revision} not found!")
            return None
        base_revision = request.actions[0].tgt_rev

        self.logger.debug(
            f"{head_project}/{head_package}@{head_revision} -> {base_project}/{base_package}@{base_revision}"
        )

        orphans = self._gitea_validate(
            head_project,
            head_package,
            head_revision,
            base_project,
            base_package,
            base_revision,
        )

        is_valid = len(orphans) == 0

        if is_valid:
            self.review_messages["accepted"] = "The change does not introduce orphan packages."
        else:
            self.review_messages["declined"] = f"Missing maintainership information for {', '.join(orphans)}." + \
                                               f" Please edit {MAINTAINERSHIP_FILE} and resubmit."

        return is_valid

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        if self.platform.name == "GITEA":
            return self._gitea_check_source_submission(src_project, src_package, src_rev, target_project, target_package)
        else:
            return self._obs_check_source_submission(src_project, src_package, src_rev, target_project, target_package)


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = CheckerBugowner


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
