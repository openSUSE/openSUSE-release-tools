#!/usr/bin/env python3

import argparse
import asyncio
import datetime
import fnmatch
import functools
import hashlib
import itertools
import logging
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from urllib.error import HTTPError

import osc.core
import pygit2
import requests

from osclib.cache import Cache


# Add a retry wrapper for some of the HTTP actions.
def retry(func):
    def wrapper(*args, **kwargs):
        retry = 0
        while retry < 5:
            try:
                return func(*args, **kwargs)
            except HTTPError as e:
                if 500 <= e.code <= 599:
                    retry += 1
                    logging.warning(
                        f"HTTPError {e.code} -- Retrying {args[0]} ({retry})"
                    )
                    # TODO: remove when move to async
                    time.sleep(0.5)
                else:
                    raise
            except OSError as e:
                if "[Errno 101]" in str(e):  # sporadically hits cloud VMs :(
                    retry += 1
                    logging.warning(f"OSError {e} -- Retrying {args[0]} ({retry})")
                    # TODO: remove when move to async
                    time.sleep(0.5)
                else:
                    raise

    return wrapper


osc.core.http_GET = retry(osc.core.http_GET)


BINARY = {
    ".7z",
    ".bsp",
    ".bz2",
    ".gem",
    ".gz",
    ".jar",
    ".lz",
    ".lzma",
    ".obscpio",
    ".oxt",
    ".pdf",
    ".png",
    ".rpm",
    ".tbz",
    ".tbz2",
    ".tgz",
    ".ttf",
    ".txz",
    ".whl",
    ".xz",
    ".zip",
    ".zst",
}

LFS_SUFFIX = "filter=lfs diff=lfs merge=lfs -text"

URL_OBS = "https://api.opensuse.org"
URL_IBS = "https://api.suse.de"

# The order is relevant (from older to newer initial codebase)
PROJECTS = [
    ("openSUSE:Factory", "factory", URL_OBS),
    # ("SUSE:SLE-12:GA", "SLE_12", URL_IBS),
    # ("SUSE:SLE-12:Update", "SLE_12", URL_IBS),
    # ("SUSE:SLE-12-SP1:GA", "SLE_12_SP1", URL_IBS),
    # ("SUSE:SLE-12-SP1:Update", "SLE_12_SP1", URL_IBS),
    # ("SUSE:SLE-12-SP2:GA", "SLE_12_SP2", URL_IBS),
    # ("SUSE:SLE-12-SP2:Update", "SLE_12_SP2", URL_IBS),
    # ("SUSE:SLE-12-SP3:GA", "SLE_12_SP3", URL_IBS),
    # ("SUSE:SLE-12-SP3:Update", "SLE_12_SP3", URL_IBS),
    # ("SUSE:SLE-12-SP4:GA", "SLE_12_SP4", URL_IBS),
    # ("SUSE:SLE-12-SP4:Update", "SLE_12_SP4", URL_IBS),
    # ("SUSE:SLE-12-SP5:GA", "SLE_12_SP5", URL_IBS),
    # ("SUSE:SLE-12-SP5:Update", "SLE_12_SP5", URL_IBS),
    # ("SUSE:SLE-15:GA", "SLE_15", URL_IBS),
    # ("SUSE:SLE-15:Update", "SLE_15", URL_IBS),
    # ("SUSE:SLE-15-SP1:GA", "SLE_15_SP1", URL_IBS),
    # ("SUSE:SLE-15-SP1:Update", "SLE_15_SP1", URL_IBS),
    # ("SUSE:SLE-15-SP2:GA", "SLE_15_SP2", URL_IBS),
    # ("SUSE:SLE-15-SP2:Update", "SLE_15_SP2", URL_IBS),
    # ("SUSE:SLE-15-SP3:GA", "SLE_15_SP3", URL_IBS),
    # ("SUSE:SLE-15-SP3:Update", "SLE_15_SP3", URL_IBS),
    # ("SUSE:SLE-15-SP4:GA", "SLE_15_SP4", URL_IBS),
    # ("SUSE:SLE-15-SP4:Update", "SLE_15_SP4", URL_IBS),
]


def is_binary_or_large(filename, size):
    """Decide if is a binary file based on the extension or size"""
    binary_suffix = BINARY
    non_binary_suffix = {
        ".1",
        ".8",
        ".SUSE",
        ".asc",
        ".c",
        ".cabal",
        ".cfg",
        ".changes",
        ".conf",
        ".desktop",
        ".dif",
        ".diff",
        ".dsc",
        ".el",
        ".html",
        ".in",
        ".init",
        ".install",
        ".keyring",
        ".kiwi",
        ".logrotate",
        ".macros",
        ".md",
        ".obsinfo",
        ".pamd",
        ".patch",
        ".pl",
        ".pom",
        ".py",
        ".rpmlintrc",
        ".rules",
        ".script",
        ".service",
        ".sh",
        ".sig",
        ".sign",
        ".spec",
        ".sysconfig",
        ".test",
        ".txt",
        ".xml",
        ".xml",
        ".yml",
    }

    suffix = pathlib.Path(filename).suffix
    if suffix in binary_suffix:
        return True
    if suffix in non_binary_suffix:
        return False
    if size >= 6 * 1024:
        return True

    return False


def _hash(hash_alg, file_or_path):
    h = hash_alg()

    def __hash(f):
        while chunk := f.read(1024 * 4):
            h.update(chunk)

    if hasattr(file_or_path, "read"):
        __hash(file_or_path)
    else:
        with file_or_path.open("rb") as f:
            __hash(f)
    return h.hexdigest()


md5 = functools.partial(_hash, hashlib.md5)
sha256 = functools.partial(_hash, hashlib.sha256)


def _files_hash(hash_alg, dirpath):
    """List of (filepath, md5) for a directory"""
    # TODO: do it async or multythread
    files = [f for f in dirpath.iterdir() if f.is_file()]
    return [(f.parts[-1], hash_alg(f)) for f in files]


files_md5 = functools.partial(_files_hash, md5)
files_sha256 = functools.partial(_files_hash, sha256)


class Git:
    """Local git repository"""

    def __init__(self, path, committer=None, committer_email=None):
        self.path = pathlib.Path(path)
        self.committer = committer
        self.committer_email = committer_email

        self.repo = None

    def is_open(self):
        return self.repo is not None

    # TODO: Extend it to packages and files
    def exists(self):
        """Check if the path is a valid git repository"""
        return (self.path / ".git").exists()

    def create(self):
        """Create a local git repository"""
        self.path.mkdir(parents=True, exist_ok=True)
        # Convert the path to string, to avoid some limitations in
        # older pygit2
        self.repo = pygit2.init_repository(str(self.path))
        return self

    def is_dirty(self):
        """Check if there is something to commit"""
        assert self.is_open()

        return self.repo.status()

    def branches(self):
        return list(self.repo.branches)

    def branch(self, branch, commit=None):
        if not commit:
            commit = self.repo.head
        else:
            commit = self.repo.get(commit)
        self.repo.branches.local.create(branch, commit)

    def checkout(self, branch):
        """Checkout into the branch HEAD"""
        new_branch = False
        ref = f"refs/heads/{branch}"
        if branch not in self.branches():
            self.repo.references["HEAD"].set_target(ref)
            new_branch = True
        else:
            self.repo.checkout(ref)
        return new_branch

    def commit(
        self,
        user,
        user_email,
        user_time,
        message,
        parents=None,
        committer=None,
        committer_email=None,
        committer_time=None,
        allow_empty=False,
    ):
        """Add all the files and create a new commit in the current HEAD"""
        assert allow_empty or self.is_dirty()

        if not committer:
            committer = self.committer if self.committer else self.user
            committer_email = (
                self.committer_email if self.committer_email else self.user_email
            )
            committer_time = committer_time if committer_time else user_time

        try:
            self.repo.index.add_all()
        except pygit2.GitError as e:
            if not allow_empty:
                raise e

        self.repo.index.write()
        author = pygit2.Signature(user, user_email, int(user_time.timestamp()))
        committer = pygit2.Signature(
            committer, committer_email, int(committer_time.timestamp())
        )
        if not parents:
            try:
                parents = [self.repo.head.target]
            except pygit2.GitError as e:
                parents = []
                if not allow_empty:
                    raise e

        tree = self.repo.index.write_tree()
        return self.repo.create_commit(
            "HEAD", author, committer, message, tree, parents
        )

    def merge(
        self,
        user,
        user_email,
        user_time,
        message,
        commit,
        committer=None,
        committer_email=None,
        committer_time=None,
        clean_on_conflict=True,
        merged=False,
        allow_empty=False,
    ):
        new_branch = False

        if not merged:
            try:
                self.repo.merge(commit)
            except KeyError:
                # If it is the first commit, we will have a missing
                # "HEAD", but the files will be there.  We can proceed
                # to the commit directly.
                new_branch = True

        if not merged and self.repo.index.conflicts:
            for conflict in self.repo.index.conflicts:
                conflict = [c for c in conflict if c]
                if conflict:
                    logging.info(f"CONFLICT {conflict[0].path}")

            if clean_on_conflict:
                self.clean()
            # Now I miss Rust enums
            return "CONFLICT"

        # Some merges are empty in OBS (no chages, not sure
        # why), for now we signal them
        if not allow_empty and not self.is_dirty():
            # I really really do miss Rust enums
            return "EMPTY"

        if new_branch:
            parents = [commit]
        else:
            parents = [
                self.repo.head.target,
                commit,
            ]
        commit = self.commit(
            user,
            user_email,
            user_time,
            message,
            parents,
            committer,
            committer_email,
            committer_time,
            allow_empty=allow_empty,
        )

        return commit

    def merge_abort(self):
        self.repo.state_cleanup()

    def last_commit(self):
        try:
            return self.repo.head.target
        except:
            return None

    def gc(self):
        logging.info(f"Garbage recollec and repackage {self.path}")
        subprocess.run(
            ["git", "gc", "--auto"],
            cwd=self.path,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

    def clean(self):
        for path, _ in self.repo.status().items():
            logging.debug(f"Cleaning {path}")
            try:
                (self.path / path).unlink()
                self.repo.index.remove(path)
            except Exception as e:
                logging.warning(f"Error removing file {path}: {e}")

    def add(self, filename):
        self.repo.index.add(filename)

    def add_default_lfs_gitattributes(self, force=False):
        if not (self.path / ".gitattributes").exists() or force:
            with (self.path / ".gitattributes").open("w") as f:
                content = ["## Default LFS"]
                content += [f"*{b} {LFS_SUFFIX}" for b in sorted(BINARY)]
                f.write("\n".join(content))
                f.write("\n")
            self.add(".gitattributes")

    def add_specific_lfs_gitattributes(self, binaries):
        self.add_default_lfs_gitattributes(force=True)
        if binaries:
            with (self.path / ".gitattributes").open("a") as f:
                content = ["## Specific LFS patterns"]
                content += [f"{b} {LFS_SUFFIX}" for b in sorted(binaries)]
                f.write("\n".join(content))
                f.write("\n")
        self.add(".gitattributes")

    def get_specific_lfs_gitattributes(self):
        with (self.path / ".gitattributes").open() as f:
            patterns = [
                line.split()[0]
                for line in f
                if line.strip() and not line.startswith("#")
            ]
        binary = {f"*{b}" for b in BINARY}
        return [p for p in patterns if p not in binary]

    def add_lfs(self, filename, sha256, size):
        with (self.path / filename).open("w") as f:
            f.write("version https://git-lfs.github.com/spec/v1\n")
            f.write(f"oid sha256:{sha256}\n")
            f.write(f"size {size}\n")
        self.add(filename)

        if not self.is_lfs_tracked(filename):
            logging.debug(f"Add specific LFS file {filename}")
            specific_patterns = self.get_specific_lfs_gitattributes()
            specific_patterns.append(filename)
            self.add_specific_lfs_gitattributes(specific_patterns)

    def is_lfs_tracked(self, filename):
        with (self.path / ".gitattributes").open() as f:
            patterns = (
                line.split()[0]
                for line in f
                if line.strip() and not line.startswith("#")
            )
            return any(fnmatch.fnmatch(filename, line) for line in patterns)

    def remove(self, filename):
        self.repo.index.remove(filename)
        (self.path / filename).unlink()

        patterns = self.get_specific_lfs_gitattributes()
        if filename in patterns:
            patterns.remove(filename)
            self.add_specific_lfs_gitattributes(patterns)


class OBS:
    def __init__(self, url=None):
        if url:
            self.change_url(url)

    def change_url(self, url):
        self.url = url
        osc.conf.get_config(override_apiurl=url)

    def _xml(self, url_path, **params):
        url = osc.core.makeurl(self.url, [url_path], params)
        logging.debug(f"GET {url}")
        return ET.parse(osc.core.http_GET(url)).getroot()

    def _meta(self, project, package, **params):
        try:
            root = self._xml(f"source/{project}/{package}/_meta", **params)
        except HTTPError:
            logging.error(f"Package [{project}/{package} {params}] has no meta")
            return None
        return root

    def _history(self, project, package, **params):
        try:
            root = self._xml(f"source/{project}/{package}/_history", **params)
        except HTTPError:
            logging.error(f"Package [{project}/{package} {params}] has no history")
            return None
        return root

    def _link(self, project, package, rev):
        try:
            root = self._xml(f"source/{project}/{package}/_link", rev=rev)
        except HTTPError:
            logging.info("Package has no link")
            return None
        except ET.ParseError:
            logging.error(
                f"Package [{project}/{package} rev={rev}] _link can't be parsed"
            )
        return root

    def _request(self, requestid):
        try:
            root = self._xml(f"request/{requestid}")
        except HTTPError:
            logging.warning(f"Cannot fetch request {requestid}")
            return None
        return root

    def exists(self, project, package):
        root = self._meta(project, package)
        if root is None:
            return False
        return root.get("project") == project

    def devel_project(self, project, package):
        root = self._meta(project, package)
        return root.find("devel").get("project")

    def request(self, requestid):
        root = self._request(requestid)
        if root is not None:
            return Request().parse(root)

    def files(self, project, package, revision):
        root = self._xml(f"source/{project}/{package}", rev=revision, expand=1)
        return [
            (e.get("name"), int(e.get("size")), e.get("md5"))
            for e in root.findall("entry")
        ]

    def _download(self, project, package, name, revision):
        url = osc.core.makeurl(
            self.url,
            ["source", project, package, urllib.parse.quote(name)],
            {"rev": revision, "expand": 1},
        )
        return osc.core.http_GET(url)

    def download(self, project, package, name, revision, dirpath):
        with (dirpath / name).open("wb") as f:
            f.write(self._download(project, package, name, revision).read())


class ProxySHA256:
    def __init__(self, obs, url=None, enabled=True):
        self.obs = obs
        self.url = url if url else "http://source.dyn.cloud.suse.de"
        self.enabled = enabled
        self.hashes = None
        self.texts = set()

    def load_package(self, package):
        logging.info("Retrieve all previously defined SHA256")
        response = requests.get(f"http://source.dyn.cloud.suse.de/package/{package}")
        if response.status_code == 200:
            json = response.json()
            self.hashes = json["shas"]
            self.texts = set(json["texts"])

    def get(self, package, name, file_md5):
        key = f"{file_md5}-{name}"
        if self.hashes is None:
            if self.enabled:
                self.load_package(package)
            else:
                self.hashes = {}
        return self.hashes.get(key, None)

    def _proxy_put(self, project, package, name, revision, file_md5, size):
        quoted_name = urllib.parse.quote(name)
        url = f"{self.obs.url}/public/source/{project}/{package}/{quoted_name}?rev={revision}"
        response = requests.put(
            self.url,
            data={
                "hash": file_md5,
                "filename": name,
                "url": url,
                "package": package,
            },
        )
        if response.status_code != 200:
            raise Exception(f"Redirector error on {self.url} for {url}")

        key = (file_md5, name)
        self.hashes[key] = {
            "sha256": response.content.decode("utf-8"),
            "fsize": size,
        }
        return self.hashes[key]

    def _obs_put(self, project, package, name, revision, file_md5, size):
        key = (file_md5, name)
        self.hashes[key] = {
            "sha256": sha256(self.obs._download(project, package, name, revision)),
            "fsize": size,
        }
        return self.hashes[key]

    def put(self, project, package, name, revision, file_md5, size):
        if not self.enabled:
            return self._obs_put(project, package, name, revision, file_md5, size)
        return self._proxy_put(project, package, name, revision, file_md5, size)

    def is_text(self, filename):
        return filename in self.texts

    def get_or_put(self, project, package, name, revision, file_md5, size):
        result = self.get(package, name, file_md5)
        if not result:
            result = self.put(project, package, name, revision, file_md5, size)

        # Sanity check
        if result["fsize"] != size:
            raise Exception(f"Redirector has different size for {name}")

        return result


class Request:
    def parse(self, xml):
        self.requestid = int(xml.get("id"))
        self.creator = xml.get("creator")

        self.type_ = xml.find("action").get("type")
        if self.type_ == "delete":
            # not much to do
            return self

        self.source = xml.find("action/source").get("project")
        # expanded MD5 or commit revision
        self.revisionid = xml.find("action/source").get("rev")

        self.target = xml.find("action/target").get("project")

        self.state = xml.find("state").get("name")

        # TODO: support muti-action requests
        # TODO: parse review history
        # TODO: add description
        return self

    def type(self):
        return self.type_

    def __str__(self):
        return f"Req {self.requestid} {self.creator} {self.type_} {self.source}->{self.target} {self.state}"

    def __repr__(self):
        return f"[{self.__str__()}]"


class Revision:
    def __init__(self, obs, history, project, package):
        self.obs = obs
        self.history = history
        self.project = project
        self.package = package

        self.commit = None
        self.ignored = False

    def parse(self, xml):
        self.rev = int(xml.get("rev"))
        # Replaced in check_expanded
        self.srcmd5 = xml.find("srcmd5").text
        self.version = xml.find("version").text

        time = int(xml.find("time").text)
        self.time = datetime.datetime.fromtimestamp(time)

        userid = xml.find("user")
        if userid is not None:
            self.userid = userid.text
        else:
            self.userid = "unknown"

        comment = xml.find("comment")
        if comment is not None:
            self.comment = comment.text or ""
        else:
            self.comment = ""

        # Populated by check_link
        self.linkrev = None

        self.requestid = None
        requestid = xml.find("requestid")
        if requestid is not None:
            self.requestid = int(requestid.text)
        else:
            # Sometimes requestid is missing, but can be extracted
            # from "comment"
            matched = re.match(
                r"^Copy from .* based on submit request (\d+) from user .*$",
                self.comment,
            )
            if matched:
                self.requestid = int(matched.group(1))

        return self

    def __str__(self):
        return f"Rev {self.project}/{self.rev} Md5 {self.srcmd5} {self.time} {self.userid} {self.requestid}"

    def __repr__(self):
        return f"[{self.__str__()}]"

    def check_link(self):
        """Add 'linkrev' attribute into the revision"""
        try:
            root = self.obs._xml(
                f"source/{self.project}/{self.package}/_link", rev=self.srcmd5
            )
        except HTTPError:
            logging.debug("No _link for the revision")
            return None
        except ET.ParseError:
            logging.error(
                f"_link can't be parsed [{self.project}/{self.package} rev={self.srcmd5}]"
            )
            raise

        target_project = root.get("project")
        rev = self.history.find_last_rev_after_time(target_project, self.time)
        if rev:
            logging.debug(f"Linkrev found: {rev}")
            self.linkrev = rev.srcmd5

    def check_expanded(self):
        # Even if it's not a link we still need to check the expanded
        # srcmd5 as it's possible used in submit requests
        self.check_link()

        # If there is a "linkrev", "rev" is ignored
        params = {"rev": self.srcmd5, "expand": "1"}
        if self.linkrev:
            params["linkrev"] = self.linkrev

        try:
            root = self.obs._xml(f"source/{self.project}/{self.package}", **params)
        except HTTPError:
            logging.error(
                f"Package [{self.project}/{self.package} {params}] can't be expanded"
            )
            raise

        self.srcmd5 = root.get("srcmd5")


class History:
    """Store the history of revisions of a package in different
    projects.

    """

    def __init__(self, obs, package):
        self.obs = obs
        self.package = package

        self.revisions = {}

    def __contains__(self, project):
        return project in self.revisions

    def __getitem__(self, project):
        return self.revisions[project]

    def _extract_copypac(self, comment):
        original_project = re.findall(
            r"osc copypac from project:(.*) package:", comment
        )
        return original_project[0] if original_project else None

    def _fetch_revisions(self, project, **params):
        root = self.obs._history(project, self.package, **params)
        if root is not None:
            return [
                Revision(self.obs, self, project, self.package).parse(r)
                for r in root.findall("revision")
            ]

    def fetch_revisions(self, project, follow_copypac=False):
        """Get the revision history of a package"""
        if project in self:
            return

        revs = self._fetch_revisions(project)
        self.revisions[project] = revs
        # while (
        #     revs
        #     and follow_copypac
        #     and (copypac_project := self._extract_copypac(revs[0].comment))
        # ):
        #     # Add the history pre-copypac
        #     # TODO: missing the old project name
        #     revs = self._fetch_revisions(copypac_project, deleted=1)
        #     self.revisions[project] = (
        #         revs + self.revisions[project]
        #     )

    def fetch_all_revisions(self, projects):
        """Pre-populate the history"""
        for project, _, api_url in projects:
            self.obs.change_url(api_url)
            self.fetch_revisions(project)

    def sort_all_revisions(self):
        """Sort revisions for all projects, from older to newer"""
        return sorted(itertools.chain(*self.revisions.values()), key=lambda x: x.time)

    def find_revision(self, project, revisionid, accepted_at):
        last_commited_revision = None
        for r in self.revisions.get(project, []):
            logging.debug(f"Find revision {revisionid} [{accepted_at}]: {r}")
            if str(r.rev) == str(revisionid) or r.srcmd5 == revisionid:
                if r.ignored:
                    logging.debug(
                        f"{r} fits but is ignored, returning {last_commited_revision}"
                    )
                    return last_commited_revision
                else:
                    logging.debug(f"{r} fits")
                    return r
            if r.time > accepted_at:
                # if we can't find the right revision, we take the last
                # commit. Before ~2012 the data was tracked really loosely
                # (e.g. using different timezones and the state field was
                # only introduced in 2016...)
                logging.warning(
                    f"Deploying workaround for missing request revision - returning {last_commited_revision}"
                )
                return last_commited_revision
            if r.commit:
                last_commited_revision = r
        logging.info("No commited revision found, returning None")
        return None

    def find_last_rev_after_time(self, project, time):
        # revs = self.projects.get(project, [])
        # return next((r for r in reversed(revs) if r.time <= time), None)
        prev = None
        for rev in self.revisions.get(project, []):
            if rev.time > time:
                return prev
            if rev.time == time:
                return rev
            prev = rev
        return prev


class Importer:
    def __init__(self, projects, package, repodir, search_ancestor, rebase_devel):
        # The idea is to create each commit in order, and draw the
        # same graph described by the revisions timeline.  For that we
        # need first to fetch all the revisions and sort them
        # linearly, based on the timestamp.
        #
        # After that we recreate the commits, and if one revision is a
        # request that contains a target inside the projects in the
        # "history", we create a merge commit.
        #
        # Optionally, if a flag is set, we will try to find a common
        # "Initial commit" from a reference branch (the first one in
        # "projects", that is safe to assume to be "openSUSE:Factory".
        # This is not always a good idea.  For example, in a normal
        # situation the "devel" project history is older than
        # "factory", and we can root the tree on it.  But for some
        # other projects we lost partially the "devel" history project
        # (could be moved), and "factory" is not the root.

        self.package = package
        self.search_ancestor = search_ancestor
        self.rebase_devel = rebase_devel

        self.obs = OBS()
        self.git = Git(
            repodir,
            committer="Git OBS Bridge",
            committer_email="obsbridge@suse.de",
        ).create()
        self.proxy_sha256 = ProxySHA256(self.obs, enabled=True)

        self.history = History(self.obs, self.package)

        # Add the "devel" project
        (project, branch, api_url) = projects[0]
        assert project == "openSUSE:Factory"
        self.obs.change_url(api_url)
        devel_project = self.obs.devel_project(project, package)
        self.projects = [(devel_project, "devel", api_url)] + projects

        # Associate the branch and api_url information per project
        self.projects_info = {
            project: (branch, api_url) for (project, branch, api_url) in self.projects
        }

    def download(self, revision):
        obs_files = self.obs.files(revision.project, revision.package, revision.srcmd5)
        git_files = {
            (f.name, f.stat().st_size, md5(f))
            for f in self.git.path.iterdir()
            if f.is_file() and f.name not in (".gitattributes")
        }

        # Overwrite ".gitattributes" with the
        self.git.add_default_lfs_gitattributes(force=True)

        # Download each file in OBS if it is not a binary (or large)
        # file
        for (name, size, file_md5) in obs_files:
            # have such files been detected as text mimetype before?
            is_text = self.proxy_sha256.is_text(name)
            if not is_text and is_binary_or_large(name, size):
                file_sha256 = self.proxy_sha256.get_or_put(
                    revision.project,
                    revision.package,
                    name,
                    revision.srcmd5,
                    file_md5,
                    size,
                )
                self.git.add_lfs(name, file_sha256["sha256"], size)
            else:
                if (name, size, file_md5) not in git_files:
                    print(f"Download {name}")
                    self.obs.download(
                        revision.project,
                        revision.package,
                        name,
                        revision.srcmd5,
                        self.git.path,
                    )
                    # Validate the MD5 of the downloaded file
                    if md5(self.git.path / name) != file_md5:
                        raise Exception(f"Download error in {name}")
                    self.git.add(name)

        # Remove extra files
        obs_names = {n for (n, _, _) in obs_files}
        git_names = {n for (n, _, _) in git_files}
        for name in git_names - obs_names:
            print(f"Remove {name}")
            self.git.remove(name)

    def import_all_revisions(self, gc):
        # Fetch all the requests and sort them.  Ideally we should
        # build the graph here, to avoid new commits before the merge.
        # For now we will sort them and invalidate the commits if
        # "rebase_devel" is set.
        self.history.fetch_all_revisions(self.projects)
        revisions = self.history.sort_all_revisions()

        logging.debug(f"Selected import order for {self.package}")
        for revision in revisions:
            logging.debug(revision)

        gc_cnt = gc
        for revision in revisions:
            gc_cnt -= 1
            if gc_cnt <= 0 and gc:
                self.git.gc()
                gc_cnt = gc
            self.import_revision(revision)

    def import_new_revision_with_request(self, revision, request):
        """Create a new branch as a result of a merge"""

        submitted_revision = self.history.find_revision(
            request.source, request.revisionid, revision.time
        )
        if not submitted_revision:
            logging.warning(f"Request {request} does not connect to a known revision")
            return False

        if not submitted_revision.commit:
            # If the revision appointed by the request is not part of
            # the git history, we can have an ordering problem.  One
            # example is "premake4".
            self.import_revision(submitted_revision)

        assert submitted_revision.commit is not None

        project = revision.project
        branch, _ = self.projects_info[project]

        # TODO: add an empty commit marking the acceptenace of the request (see discussion in PR 2858)
        self.git.branch(branch, submitted_revision.commit)
        self.git.clean()
        self.git.checkout(branch)

        logging.info(f"Create new branch based on {submitted_revision.commit}")
        revision.commit = submitted_revision.commit

    def _rebase_branch_history(self, project, revision):
        branch, _ = self.projects_info[project]
        history = self.history[project]
        revision_index = history.index(revision)
        for index in range(revision_index + 1, len(history)):
            revision = history[index]
            # We are done when we have one non-commited revision
            if not revision.commit:
                return
            logging.info(f"Rebasing {revision} from {branch}")
            revision.commit = None
            self.import_revision(revision)

    def import_revision_with_request(self, revision, request):
        """Import a single revision via a merge"""

        submitted_revision = self.history.find_revision(
            request.source, request.revisionid, revision.time
        )
        if not submitted_revision:
            logging.warning(f"Request {request} does not connect to a known revision")
            return False
        assert submitted_revision.commit is not None

        # TODO: detect a revision, case in point
        # Base:System/bash/284 -> rq683701 -> accept O:F/151
        #   -> autocommit Base:System/bash/285
        # Revert lead to openSUSE:Factory/bash/152
        # Base:System/286 restored the reverted code in devel project
        # rq684575 was created and accepted as O:F/153
        # But the 284-285 and the 285-286 changeset is seen as empty
        # as the revert was never in Base:System, so the
        # submitted_revision of 684575 has no commit
        if submitted_revision.commit == "EMPTY":
            logging.warning("Empty commit submitted?!")
            return False

        message = (
            f"Accepting request {revision.requestid}: {revision.comment}\n\n{revision}"
        )
        commit = self.git.merge(
            # TODO: revision.userid or request.creator?
            f"OBS User {revision.userid}",
            "null@suse.de",
            revision.time,
            message,
            submitted_revision.commit,
        )

        if commit == "EMPTY":
            logging.warning("Empty merge. Ignoring the revision and the request")
            self.git.merge_abort()
            revision.commit = commit
            return False

        if commit == "CONFLICT":
            logging.info("Merge conflict. Downloading revision")
            self.download(revision)
            message = f"CONFLICT {message}"
            commit = self.git.merge(
                f"OBS User {revision.userid}",
                "null@suse.de",
                revision.time,
                message,
                submitted_revision.commit,
                merged=True,
            )

        assert commit and commit != "CONFLICT"
        logging.info(f"Merge with {submitted_revision.commit} into {commit}")
        revision.commit = commit

        # TODO: There are more checks to do, like for example, the
        # last commit into the non-devel branch should be a merge from
        # the devel branch
        if self.rebase_devel:
            branch, _ = self.projects_info.get(request.source, (None, None))
            if branch == "devel":
                self.git.repo.references[f"refs/heads/{branch}"].set_target(commit)
                self._rebase_branch_history(request.source, submitted_revision)

        return True

    def matching_request(self, revision):
        request = self.obs.request(revision.requestid)
        if not request:
            return None

        # to be handled by the caller
        if request.type() != "submit":
            return request

        if request.source not in self.projects_info:
            logging.info("Request from a non exported project")
            return None

        if request.target != revision.project:
            # This seems to happen when the devel project gets
            # reinitialized (for example, SR#943593 in 7zip, or
            # SR#437901 in ColorFull)
            logging.info("Request target different from current project")
            return None

        if request.source == request.target:
            # this is not a merge, but a different way to do a
            # contribution to the (devel) project - see bindfs's rev 1
            logging.info("Request within the same project")
            return None

        return request

    def import_revision(self, revision):
        """Import a single revision into git"""
        project = revision.project
        branch, api_url = self.projects_info[project]

        logging.info(f"Importing [{revision}] to {branch}")

        self.obs.change_url(api_url)

        # Populate linkrev and replace srcmd5 from the linked
        # revision.  If somehow fails, the revision will be ignored
        # and not imported.
        try:
            revision.check_expanded()
        except Exception:
            logging.warning("Broken revision")
            revision.ignored = True
            return

        # When doing a SR, we see also a revision in the origin
        # project with the outgoing request, but without changes in
        # the project.  We can ignore them.
        #
        # If there is a request ID, it will be filtered out later,
        # when the target project is different from itself.
        if revision.userid == "autobuild" and not revision.requestid:
            logging.info("Ignoring autocommit")
            revision.ignored = True
            return

        if revision.userid == "buildservice-autocommit":
            logging.info("Ignoring autocommit")
            revision.ignored = True
            return

        # Create the reference if the branch is new.  If so return
        # True.
        new_branch = self.git.checkout(branch)

        if revision.requestid:
            request = self.matching_request(revision)
            if request:
                if request.type() == "delete":
                    # TODO: after this comes a restore, this should be collapsed
                    # before even hitting git
                    logging.info("Delete request ignored")
                    revision.ignored = True
                    return

                logging.debug(f"Found matching request: #{revision.project} #{request}")
                if new_branch:
                    self.import_new_revision_with_request(revision, request)
                    return
                if self.import_revision_with_request(revision, request):
                    return

        # Import revision as a single commit (without merging)
        self.download(revision)

        if new_branch or self.git.is_dirty():
            commit = self.git.commit(
                f"OBS User {revision.userid}",
                "null@suse.de",
                revision.time,
                # TODO: Normalize better the commit message
                f"{revision.comment}\n\n{revision}",
                # Create an empty commit only if is a new branch
                allow_empty=new_branch,
            )
            revision.commit = commit
            logging.info(f"Commit {commit}")
        else:
            logging.info("Skip empty commit")
            revision.ignored = True


def main():
    parser = argparse.ArgumentParser(description="OBS history importer into git")
    parser.add_argument("package", help="OBS package name")
    parser.add_argument(
        "-r",
        "--repodir",
        required=False,
        type=pathlib.Path,
        help="Local git repository directory",
    )
    parser.add_argument(
        "-f",
        "--force",
        action="store_true",
        help="If the repository directory exists, remove it",
    )
    parser.add_argument(
        "-a",
        "--search-ancestor",
        action="store_true",
        help="Search closest ancestor candidate for initial commit",
    )
    parser.add_argument(
        "-d",
        "--rebase-devel",
        action="store_true",
        help="The devel project with be rebased after a merge",
    )
    parser.add_argument(
        "-g",
        "--gc",
        metavar="N",
        type=int,
        default=200,
        help="Garbage recollect and pack the git history each N commits",
    )
    parser.add_argument(
        "--level",
        "-l",
        default="INFO",
        help="logging level",
    )

    args = parser.parse_args()

    if args.level:
        numeric_level = getattr(logging, args.level.upper(), None)
        if not isinstance(numeric_level, int):
            print(f"Invalid log level: {args.level}")
            sys.exit(-1)
        logging.basicConfig(level=numeric_level)
        if numeric_level == logging.DEBUG:
            osc.conf.config["debug"] = True
            requests_log = logging.getLogger("requests.packages.urllib3")
            requests_log.setLevel(logging.DEBUG)
            requests_log.propagate = True

    if not args.repodir:
        args.repodir = pathlib.Path(args.package)

    if args.repodir.exists() and not args.force:
        print(f"Repository {args.repodir} already present")
        sys.exit(-1)
    elif args.repodir.exists() and args.force:
        logging.info(f"Removing old repository {args.repodir}")
        shutil.rmtree(args.repodir)

    Cache.init()

    # TODO: use a CLI parameter to describe the projects
    importer = Importer(
        PROJECTS, args.package, args.repodir, args.search_ancestor, args.rebase_devel
    )
    importer.import_all_revisions(args.gc)


if __name__ == "__main__":
    main()
