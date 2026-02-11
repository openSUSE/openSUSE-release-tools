#!/usr/bin/python3

# SPDX-License-Identifier: MIT

import json
import os
import shutil
import sys
import re
from pathlib import Path
from typing import List, Set

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

    def _gitea_clone(self, project: str, package: str, revision: str):
        local_dir = Path(
            os.path.expanduser(
                f"~/.cache/bugowner_checker_git/{project}_{package}"
            )
        )
        if local_dir.is_dir():
            self.logger.warning(f"directory {local_dir} already exists, removing it.")
            shutil.rmtree(local_dir, ignore_errors=True)

        local_dir.mkdir(parents=True)

        return self.scm.clone_repository(
            url=self.scm.package_url(project, package),
            dstpath=local_dir,
            revision=revision,
        )

    def _diff_submodules(self, repo, base_revision, head_project, head_package, head_revision):
        diff = self.scm.submodule_diff(repo, base_revision, head_project, self.scm.package_url(head_project, head_package), head_revision)

        new_submodules = set()
        updated_submodules = set()
        for line in diff.splitlines():
            if line.startswith("Submodule"):
                # Extract name from 'Submodule tiff 0000000...7c7b21a (new submodule)'
                splitted_line = line.strip().split()
                name = splitted_line[1]
                changes = " ".join(splitted_line[3:])

                if changes == "(new submodule)":
                    new_submodules.add(name)
                    self.logger.info(f"Found new submodule: {name}")
                elif changes == "(commits not present)":
                    updated_submodules.add(name)
                    self.logger.info(f"Found updated submodule: {name}")
                else:
                    self.logger.error(f"Unknown submodule change type: {line}")

        if len(new_submodules) == 0:
            self.logger.info("No new submodules were found.")
        if len(updated_submodules) == 0:
            self.logger.info("No updated submodules were found.")

        shared_submodules = new_submodules.intersection(updated_submodules)
        if len(shared_submodules) > 0:
            self.logger.warning(f"It looks like these submodules '{' ,'.join(shared_submodules)}' are both new and updated. This may " +
                                "indicate a parsing error in the bugowner_checker bot.")

        return new_submodules, updated_submodules

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
        referenced_packages: List[str],
        head_project: str,
        head_package: str,
        head_revision: str,
        base_project: str,
        base_package: str,
        base_revision: str,
    ) -> bool:
        referenced_packages = set(referenced_packages)
        repo = self._gitea_clone(base_project, base_package, revision=base_revision)

        new_submodules, updated_submodules = self._diff_submodules(
            repo, base_revision, head_project, head_package, head_revision
        )

        repo.git.checkout(head_revision)

        maintained = self._load_maintainership_data(Path(repo.working_tree_dir, MAINTAINERSHIP_FILE))
        whitelisted = self._load_whitelist_data(Path(repo.working_tree_dir, WHITELIST_FILE))

        validated_packages = set()
        orphan_packages = set()
        changed_submodules = new_submodules.union(updated_submodules)

        for package in referenced_packages:
            if package not in changed_submodules:
                raise ValueError(f"A PR for {package} is mentioned in the description but no changed submodules were detected. Aborting...")

        for package in referenced_packages:
            if package not in maintained and package not in whitelisted:
                orphan_packages.add(package)
            else:
                validated_packages.add(package)

        return validated_packages, orphan_packages

    def _gitea_check_source_submission(
        self,
        head_project: str,
        head_package: str,
        head_revision: str,
        base_project: str,
        base_package: str,
    ) -> bool:

        base_revision = self.request.actions[0].tgt_rev

        referenced_prs = [line for line in self.request.description.splitlines() if line.startswith("PR: ")]
        referenced_packages = [pr.split("/")[1].split("!")[0] for pr in referenced_prs]
        self.logger.debug(f"packages: {referenced_packages}")

        self.logger.debug(
            f"{head_project}/{head_package}@{head_revision} -> {base_project}/{base_package}@{base_revision}"
        )

        validated_packages, orphans = self._gitea_validate(
            referenced_packages,
            head_project,
            head_package,
            head_revision,
            base_project,
            base_package,
            base_revision,
        )

        is_valid = len(orphans) == 0

        if is_valid:
            self.review_messages["accepted"] = "The change does not introduce orphan packages. " + \
                                               f"The following packages were checked and are covered either in `{MAINTAINERSHIP_FILE}`" + \
                                               f" or `{WHITELIST_FILE}`:\n\n" + "\n".join(" - `" + p + "`" for p in validated_packages) + \
                                               "\n"
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
