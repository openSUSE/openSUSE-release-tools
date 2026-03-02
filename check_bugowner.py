#!/usr/bin/python3

# SPDX-License-Identifier: MIT

import datetime
import json
import os
import sys
import re
from pathlib import Path
from typing import List, Set
from cmdln import CmdlnOptionParser

import ldap
import requests
from urllib.error import HTTPError

import osc.conf
import osc.core
import ReviewBot

http_GET = osc.core.http_GET
MAINTAINERSHIP_FILE = "_maintainership.json"
WHITELIST_FILE = "whitelist_maintainership.json"
LDAP_SERVER = "pan.suse.de"


class CheckerBugowner(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.request_default_return = True
        self.override_allow = False
        self.ldap = False
        self.maintained = {}
        self.whitelisted = {}
        self.ldap_cache = {"update_time": datetime.datetime.now(), "values": {}}
        self.email_cache = {"update_time": datetime.datetime.now(), "values": {}}

    @staticmethod
    def _cache_set(cache, key, value):
        cache["values"][key] = value
        cache["update_time"] = datetime.datetime.now()

    @staticmethod
    def _cache(cache):
        return cache["values"]

    @staticmethod
    def _cache_get(cache, key):
        return CheckerBugowner._cache(cache)[key]

    def _cache_clear(self, cache):
        difference = datetime.datetime.now() - cache["update_time"]
        if difference.days >= 1:
            self.logger.info(f"Cache is {difference.days} days old, clearing it...")
            cache = {"update_time": datetime.datetime.now(), "values": {}}

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

    def _gitea_checkout(
        self, owner: str, repo: str, revision: str, revision_name=None, remote="origin", remote_url=None
    ):
        local_dir = Path(
            os.path.expanduser(
                f"~/.cache/bugowner_checker_git/{owner}/{repo}"
            )
        )

        if not local_dir.is_dir():
            self.logger.info(f"directory {local_dir} does not exists, creating it.")
            local_dir.mkdir(parents=True)
            return self.scm.clone_repository(
                url=self.scm.package_url(owner, repo),
                dstpath=local_dir,
                revision=revision,
            )
        else:
            return self.scm.checkout_revision(
                local_dir, revision, revision_name=revision_name, remote=remote, remote_url=remote_url
            )

    def _git_remote_name(self, repo, project: str, url: str) -> str:
        if repo.remote("origin").url == url:
            return "origin"
        return project

    def _diff_submodules(
        self, repo, base_revision, head_project, head_package, head_revision, head_revision_name=None
    ):
        head_url = self.scm.package_url(head_project, head_package)
        diff, rebased = self.scm.submodule_diff(
            repo,
            base_revision,
            head_project,
            head_url,
            head_revision,
            head_revision_name=head_revision_name
        )

        new_submodules = set()
        updated_submodules = set()
        deleted_submodules = set()
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
                elif changes == "(submodule deleted)":
                    deleted_submodules.add(name)
                    self.logger.info(f"Found deleted submodule: {name}")
                else:
                    self.logger.error(f"Unknown submodule change type: {line}")

        if len(new_submodules) == 0:
            self.logger.info("No new submodules were found.")
        if len(updated_submodules) == 0:
            self.logger.info("No updated submodules were found.")

        shared_submodules = new_submodules.intersection(updated_submodules)
        if len(shared_submodules) > 0:
            self.logger.warning(
                f"It looks like these submodules '{' ,'.join(shared_submodules)}' are both new and updated. This may "
                + "indicate a parsing error in the bugowner_checker bot."
            )

        return new_submodules, updated_submodules, deleted_submodules, rebased

    def _load_whitelist_data(self, file: Path) -> Set[str]:
        try:
            with open(file) as f:
                # Assuming it's a JSON array (list) of strings
                data = json.load(f)

                if not isinstance(data, list):
                    raise ValueError(
                        f"Whitelist file '{WHITELIST_FILE}' must contain a JSON array/list, but {type(data)} was found."
                    )

                # Convert list of package names to a set for fast lookups
                return set(data)

        except FileNotFoundError:
            self.logger.warning(
                f"Whitelist file '{WHITELIST_FILE}' not found. Skipping the whitelist check."
            )
            return set()

    def _load_maintainership_data(self, file: Path) -> Set[str]:
        with open(file) as f:
            # Assuming it's a JSON dictionary
            data = json.load(f)

            if not isinstance(data, dict):
                raise ValueError(
                    f"Maintainership file '{MAINTAINERSHIP_FILE}' must contain a JSON dict, but {type(data)} was found."
                )

            # Convert list of package names to a set for fast lookups
            return data

    def _init_maintainership(self, repo):
        self.maintained = self._load_maintainership_data(Path(repo.working_tree_dir, MAINTAINERSHIP_FILE))
        self.whitelisted = self._load_whitelist_data(Path(repo.working_tree_dir, WHITELIST_FILE))

    def _gitea_validate(
        self,
        repo,
        referenced_packages: List[str],
        head_project: str,
        head_package: str,
        head_revision: str,
        base_project: str,
        base_package: str,
        base_revision: str,
        head_revision_name=None,
    ) -> bool:
        referenced_packages = set(referenced_packages)
        validated_packages = set()
        orphan_packages = set()

        new_submodules, updated_submodules, deleted_submodules, rebased = self._diff_submodules(
            repo, base_revision, head_project, head_package, head_revision, head_revision_name=head_revision_name
        )
        # Set of packages that changed between base and HEAD
        changed_submodules = new_submodules.union(updated_submodules)

        # Fetch and checkout HEAD
        repo = self._gitea_checkout(
            base_project,
            base_package,
            revision=head_revision,
            revision_name=head_revision_name,
            remote=head_project,
            remote_url=self.scm.package_url(head_project, head_package),
        )
        # Read _maintainership.json and whitelist.json after checking out HEAD
        self._init_maintainership(repo)

        # Set of packages to be validated
        if rebased:
            to_validate = changed_submodules
        else:
            to_validate = changed_submodules.intersection(referenced_packages)

        warnings = []
        # Validate referencing PRs
        for package in referenced_packages:
            if package not in changed_submodules:
                msg = f"A PR for {package} is mentioned in the description but no changed submodules were detected."
                self.logger.warning(msg)
                warnings.append(msg)

        # Convert list of package names to a set for fast lookups
        maintained = set(self.maintained.keys())

        # Validate changes
        for package in to_validate:
            if package not in maintained and package not in self.whitelisted:
                orphan_packages.add(package)
            else:
                validated_packages.add(package)

        return repo, validated_packages, orphan_packages, warnings

    def _ldap_active_user(self, email):
        instance = ldap.initialize(f"ldap://{LDAP_SERVER}")

        active_statuses = []
        for e in email:
            if e:
                if e not in self._cache(self.ldap_cache).keys():
                    result = instance.search_s(
                        "OU=User accounts,DC=corp,DC=suse,DC=com",
                        ldap.SCOPE_SUBTREE,
                        filterstr=f"(mail={e})",
                        # We only need to know whether or not the submitter
                        # has an active account.
                        attrlist=["EMPLOYEESTATUS"]
                    )

                    # In case the search fails:
                    try:
                        active_list = result[0]
                    except IndexError:
                        self.logger.debug(f"LDAP search failed with {result}")
                        self._cache_set(self.ldap_cache, e, None)
                        active_statuses.append(None)
                        continue

                    if active_list:
                        name, attrs = active_list
                        self.logger.debug(f"Found LDAP user {name}")
                        self._cache_set(self.ldap_cache, e, attrs)
                    else:
                        self._cache_set(self.ldap_cache, e, None)
                    
                active_statuses.append(self._cache_get(self.ldap_cache, e))

        instance.unbind()

        return active_statuses

    def _gitea_email(self, owner):
        for o in owner:
            if o and (o not in self._cache(self.email_cache).keys()):
                try:
                    self._cache_set(self.email_cache, o, self.platform.get_user(o).email)
                except (HTTPError, requests.exceptions.HTTPError):
                    self._cache_set(self.email_cache, o, None)
        return [self._cache_get(self.email_cache, o) for o in owner]

    def _gitea_package_maintainer(self, package: str):
        in_maintainership_json = package in self.maintained.keys()
        if in_maintainership_json:
            owner = self.maintained[package]
            if self.ldap:
                try:
                    # Get owner email
                    email = self._gitea_email(owner)

                    self.logger.debug(f"{owner} -> {email}")

                    # Get owner active status
                    owner_attrs = self._ldap_active_user(email)

                    # Get users that were not found on LDAP.
                    not_found_users = [
                        e for e, s in zip(email, owner_attrs)
                        if s is None
                    ]

                    if not_found_users:
                        users = ', '.join(not_found_users)
                        self.logger.warning(f"The following emails were not found on LDAP: {users}.")

                    # Get inactive users
                    inactive_users = [
                        o for o, s in zip(owner, owner_attrs)
                        if (s and "EMPLOYEESTATUS" in s.keys() and s["EMPLOYEESTATUS"][0] != b'Active')
                    ]

                    self.logger.debug(f"Inactive users: {inactive_users}")

                    if inactive_users:
                        users = ', '.join('`' + u + '`' for u in inactive_users if u)
                        ldap_status = f" The following users are **not active** on LDAP: {users}"
                    else:
                        ldap_status = ""

                    return f"`{owner}`.{ldap_status}"

                except ldap.SERVER_DOWN:
                    self.logger.warning(f"LDAP server {LDAP_SERVER} is down...")

            return f"`{owner}`"
        else:
            maintainer = "`whitelisted`"
        return maintainer

    def _gitea_check_source_submission(
        self,
        head_project: str,
        head_package: str,
        head_revision: str,
        base_project: str,
        base_package: str,
    ) -> bool:
        # If the caches are more than a day old, clear them
        self._cache_clear(self.ldap_cache)
        self._cache_clear(self.email_cache)

        # Get the target branch or commit
        if self.request.actions[0].tgt_branch:
            base_revision = self.request.actions[0].tgt_branch
        else:
            base_revision = self.request.actions[0].tgt_rev

        # Create the repo in case it doesn't exist
        # or checkout the base revision in case it does
        repo = self._gitea_checkout(base_project, base_package, revision=base_revision)
        head_remote_name = self._git_remote_name(repo, head_project, self.scm.package_url(head_project, head_package))

        if self.request.actions[0].src_branch:
            head_revision = self.request.actions[0].src_branch

        if head_revision == base_revision:
            head_revision_name = f"{head_project}_{head_package}_{head_revision}"
        else:
            head_revision_name = None

        referenced_prs = [
            line
            for line in self.request.description.splitlines()
            if line.startswith("PR: ")
        ]
        referenced_packages = [pr.split("/")[1].split("!")[0] for pr in referenced_prs]

        self.logger.debug(
            f"{head_remote_name}/{head_package}@{head_revision} -> {base_project}/{base_package}@{base_revision}"
        )

        # Validate the diff
        repo, validated_packages, orphans, warnings = self._gitea_validate(
            repo,
            referenced_packages,
            head_remote_name,
            head_package,
            head_revision,
            base_project,
            base_package,
            base_revision,
            head_revision_name=head_revision_name
        )

        is_valid = len(orphans) == 0

        review_warnings = "\n".join("**Warning**: " + w for w in warnings) + "\n\n"

        if is_valid:
            self.review_messages["accepted"] = review_warnings + \
                "The change does not introduce orphan packages. " + \
                f"The following packages were checked and are covered either in `{MAINTAINERSHIP_FILE}`" + \
                f" or `{WHITELIST_FILE}`:\n\n" + "\n".join(
                f" - `{p}`: " +
                self._gitea_package_maintainer(p) for p in validated_packages)
            self.review_messages["accepted"] = self.review_messages["accepted"] + "\n"
        else:
            self.review_messages["declined"] = review_warnings + \
                f"Missing maintainership information for {', '.join(orphans)}." + \
                f" Please edit {MAINTAINERSHIP_FILE} and resubmit."

        # Cleanup branches
        self.scm.checkout_revision(repo, base_revision)
        repo.git.branch("-D", head_revision_name if head_revision_name else head_revision)
        repo.git.branch("-D", "-r", f"{head_remote_name}/{head_revision}")

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

    def get_optparser(self) -> CmdlnOptionParser:
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option(
            "--ldap",
            action="store_true",
            default=False,
            help=f"Query {LDAP_SERVER} to check whether a maintainer is still a SUSE employee",
        )

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        bot.ldap = self.options.ldap

        return bot


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
