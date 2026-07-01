#!/usr/bin/python3
"""Gitea ReviewBot: gate products/SLES productcompose maintainership.

Reviews products/SLES PRs. For each PR it fetches the SLFO _maintainership.json once, validates
that every maintainer login is a confirmed OBS account, and — if the PR changes
000productcompose/default.productcompose — resolves the binaries it adds to their source packages
and reports sources with no maintainer (orphans). Both checks decline the review (aggregated), with
the reason in the review body and the logs. OBS access uses the osc library; Gitea access uses the
platform API.
"""

import logging
import re
import sys
import traceback
import urllib.error

import requests

from collections import namedtuple

import ReviewBot

from osc import conf
from osc.core import http_GET, makeurl, xml_parse

DEFAULT_ALLOW_REPOS = ""
PRODUCTCOMPOSE_PATH = "000productcompose/default.productcompose"
SOURCE_PROJECT = "SUSE:SLFO:Main"
MAINTAINERSHIP_OWNER = "products"
MAINTAINERSHIP_REPO = "SLFO"
MAINTAINERSHIP_REF = "slfo-main"
MAINTAINERSHIP_FILE = "_maintainership.json"
PERSON_BATCH_SIZE = 50

# A maintainer login safe to embed in an XPath string literal.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9_.@-]+$")
# An added productcompose package line "+    - name" (name stops at whitespace or '#').
_ADDED_BINARY_RE = re.compile(r"^\+\s+-\s+([A-Za-z0-9][^\s#]*)", re.MULTILINE)

OrphanResult = namedtuple("OrphanResult", ["orphans", "failed", "checked"])
UserCheckResult = namedtuple("UserCheckResult", ["confirmed", "invalid", "not_found"])


# --------------------------------------------------------------------------- #
# Pure functions (no I/O)
# --------------------------------------------------------------------------- #


def extract_file_diff(diff_text, path):
    """Return the section of a (multi-file) unified diff for a single file ("" if absent)."""
    target = f"diff --git a/{path} b/{path}"
    section = []
    capturing = False
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("diff --git "):
            capturing = line.rstrip("\r\n") == target
        if capturing:
            section.append(line)
    return "".join(section)


def parse_added_binaries(file_diff_text):
    """Return sorted unique binary names added in a single file's diff section."""
    return sorted(set(_ADDED_BINARY_RE.findall(file_diff_text)))


def added_binaries_from_diff(diff_text):
    """Return sorted unique binaries added to the productcompose in a PR diff."""
    return parse_added_binaries(extract_file_diff(diff_text, PRODUCTCOMPOSE_PATH))


def build_source_map(root):
    """Build a {binary -> base source package} map from view=info&parse=1 XML.

    Root tag-agnostic; multibuild flavors (graphviz:qt6) are stripped to the base name.
    """
    result = {}
    for sourceinfo in root.iter("sourceinfo"):
        pkg = sourceinfo.get("package")
        if not pkg:
            continue
        pkg = pkg.split(":", 1)[0]
        for subpacks in sourceinfo.findall("subpacks"):
            binary = (subpacks.text or "").strip()
            if binary:
                result[binary] = pkg
    return result


def resolve_sources(binaries, source_map):
    """Return (sorted unique resolved sources, binaries that did not resolve)."""
    sources = set()
    failed = []
    for binary in binaries:
        source = source_map.get(binary)
        if source:
            sources.add(source)
        else:
            failed.append(binary)
    return sorted(sources), failed


def normalize_maintainership(data):
    """Return a {package -> {"users": [...], "groups": [...]}} map.

    Mirrors check_bugowner.py format detection (1.0 vs legacy unversioned). Raises ValueError on an
    obs-maintainers header with an unsupported version/document.
    """
    if {"header", "project", "packages"} <= set(data.keys()):
        header = data["header"]
        if {"document", "version"} == set(header.keys()) and header.get(
            "document"
        ) == "obs-maintainers":
            if header["version"] == "1.0":
                return data["packages"]
            raise ValueError(
                f"Unsupported maintainership file version {header['version']}"
            )
        raise ValueError(
            f"Unsupported maintainership file format {header.get('document')!r}"
        )
    return {pkg: {"users": users or [], "groups": []} for pkg, users in data.items()}


def is_orphan(db, pkg):
    """True if pkg has no maintainer (missing, falsy, or empty users AND groups)."""
    entry = db.get(pkg)
    if not entry:
        return True
    return not ((entry.get("users") or []) or (entry.get("groups") or []))


def find_orphans(sources, db):
    """Return the sorted source packages from ``sources`` that have no maintainer."""
    return sorted(src for src in sources if is_orphan(db, src))


def extract_users(db):
    """Return sorted unique maintainer logins from a normalized DB (groups ignored).

    Raises ValueError on an empty/non-string login or one unsafe to embed in an XPath literal.
    """
    seen = set()
    for pkg, entry in db.items():
        for login in entry.get("users") or []:
            if not isinstance(login, str) or not login:
                raise ValueError(f"invalid user entry {login!r} in package {pkg!r}")
            if not _LOGIN_RE.match(login):
                raise ValueError(
                    f"login {login!r} contains characters unsafe for XPath"
                )
            seen.add(login)
    return sorted(seen)


def build_person_match(users):
    """Return an OBS /search/person XPath match predicate for a batch of logins."""
    return "(" + " or ".join(f"@login='{u}'" for u in users) + ")"


def parse_persons(root):
    """Return {login: state} from an OBS /search/person response (no-login records skipped)."""
    return {
        person.findtext("login"): person.findtext("state")
        for person in root.findall("person")
        if person.findtext("login")
    }


def classify_users(users, found):
    """Classify each login as confirmed / invalid (other state) / not_found (absent)."""
    confirmed = []
    invalid = []
    not_found = []
    for user in users:
        if user not in found:
            not_found.append(user)
        elif found[user] == "confirmed":
            confirmed.append(user)
        else:
            invalid.append(user)
    return UserCheckResult(confirmed, invalid, not_found)


def find_orphan_sources(binaries, source_info_root, db):
    """Resolve added binaries to sources and return the orphans among them."""
    source_map = build_source_map(source_info_root)
    sources, failed = resolve_sources(binaries, source_map)
    return OrphanResult(find_orphans(sources, db), failed, len(sources))


def _bullets(items):
    return "\n".join(f"- {i}" for i in items)


def build_declined_message(orphans, invalid_users, not_found_users):
    """Build the aggregated decline message ("" if nothing to report)."""
    sections = []
    if orphans:
        sections.append(
            f"New source packages with no maintainer in {MAINTAINERSHIP_FILE}:\n\n"
            + _bullets(orphans)
        )
    if not_found_users:
        sections.append(
            "Maintainer logins not found in OBS:\n\n"
            + _bullets(sorted(not_found_users))
        )
    if invalid_users:
        sections.append(
            "Maintainer logins that are not confirmed OBS accounts:\n\n"
            + _bullets(sorted(invalid_users))
        )
    return "\n\n".join(sections)


def build_accepted_message(unresolved):
    """Build the acceptance message, noting any unresolved (unchecked) binaries."""
    if unresolved:
        return (
            "Maintainership check passed.\n\n"
            "Warning - binaries that could not be resolved to a source package "
            "(not checked):\n\n" + _bullets(sorted(unresolved))
        )
    return "Maintainership check passed."


# --------------------------------------------------------------------------- #
# Thin I/O wrappers (mocked in tests)
# --------------------------------------------------------------------------- #


def fetch_pr_diff(platform, owner, repo, pr_id):
    """Return the unified diff text of a Gitea pull request."""
    return platform.get_path(f"repos/{owner}/{repo}/pulls/{pr_id}.diff").text


def fetch_maintainership(platform, owner, repo, ref):
    """Fetch and normalize _maintainership.json from a Gitea repo ref (raw API)."""
    data = platform.get_path(
        f"repos/{owner}/{repo}/raw/{MAINTAINERSHIP_FILE}?ref={ref}"
    ).json()
    return normalize_maintainership(data)


def fetch_source_info(apiurl, project):
    """Return the parsed root Element of /source/<project>?view=info&parse=1."""
    url = makeurl(apiurl, ["source", project], {"view": "info", "parse": "1"})
    return xml_parse(http_GET(url)).getroot()


def query_persons(apiurl, users):
    """Return {login: state} for a batch of logins via OBS /search/person."""
    url = makeurl(apiurl, ["search", "person"], {"match": build_person_match(users)})
    return parse_persons(xml_parse(http_GET(url)).getroot())


def check_user_validity(db, apiurl, batch_size=PERSON_BATCH_SIZE):
    """Validate every maintainer login in the DB against OBS, in batches."""
    users = extract_users(db)
    confirmed = []
    invalid = []
    not_found = []
    for offset in range(0, len(users), batch_size):
        batch = users[offset:offset + batch_size]
        result = classify_users(batch, query_persons(apiurl, batch))
        confirmed.extend(result.confirmed)
        invalid.extend(result.invalid)
        not_found.extend(result.not_found)
    return UserCheckResult(confirmed, invalid, not_found)


# --------------------------------------------------------------------------- #
# Bot
# --------------------------------------------------------------------------- #


class SleCheckBot(ReviewBot.ReviewBot):
    """A review bot that gates productcompose maintainership on products/SLES PRs."""

    # ReviewBot.apiurl setter (ReviewBot.py:173-176) unconditionally accesses self.scm and
    # self.platform. Tests construct SleCheckBot via object.__new__() to bypass __init__, then
    # assign bot.apiurl directly — at that point neither scm nor platform exists yet, causing
    # AttributeError. This override guards both accesses with getattr so the setter is safe on
    # a partially-initialised instance while remaining behaviourally identical in production
    # (scm and platform are always present after ReviewBot.__init__ completes).
    # Confirmed against ReviewBot.py:106,114,138-139,149,157,173-176 and plat/gitea.py:296.
    @property
    def apiurl(self):
        return self._apiurl

    @apiurl.setter
    def apiurl(self, url):
        self._apiurl = url
        if getattr(self, "scm", None) is not None and self.scm.name == "OSC":
            self.scm.apiurl = url
        if getattr(self, "platform", None) is not None and self.platform.name == "OBS":
            self.platform.apiurl = url

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        conf.get_config()

        self.apiurl = conf.config["apiurl"]

        self.allowed_repositories = []

        # This is heavily dependent on the GITEA platform
        if self.platform.name != "GITEA":
            raise Exception("Unsupported platform: this bot is only supported on Gitea")

    @staticmethod
    def get_request_from_src_rev(requests_list, src_rev):
        for request in requests_list:
            if request.actions[0].src_rev == src_rev:
                return request
        return None

    def check_source_submission(
        self, src_owner, src_project, src_rev, target_owner, target_package
    ):
        self.logger.info(
            f"Checking {src_owner}/{src_project} -> {target_owner}/{target_package}"
        )

        try:
            result, message = self.run_check(
                src_owner, src_project, src_rev, target_owner, target_package
            )
        except (
            requests.exceptions.RequestException,
            urllib.error.HTTPError,
            urllib.error.URLError,
        ):
            self.logger.warning(
                "transient error, will retry next run:\n%s", traceback.format_exc()
            )
            return None
        except Exception:
            self.review_messages["declined"] = (
                f"Unhandled exception:\n\n```{traceback.format_exc()}```"
            )
            return False

        if result:
            self.review_messages["accepted"] = message or "OK"
        return result

    def run_check(self, src_owner, src_project, src_rev, target_owner, target_package):
        """Run both checks. Returns (result, message): True/False/None and an accept message."""
        request = self.get_request_from_src_rev(self.requests, src_rev)
        if not request:
            self.logger.warning(f"Request for src_rev {src_rev} not found")
            return None, None

        if f"{request._owner}/{request._repo}" not in self.allowed_repositories:
            self.logger.info(
                f"{request._owner}/{request._repo} is not in the allowed repositories list"
            )
            return None, None

        db = fetch_maintainership(
            self.platform, MAINTAINERSHIP_OWNER, MAINTAINERSHIP_REPO, MAINTAINERSHIP_REF
        )

        # 1. User-validity check (always).
        users = check_user_validity(db, self.apiurl)

        # 2. Orphan check (only if the PR changes the productcompose).
        binaries = added_binaries_from_diff(
            fetch_pr_diff(self.platform, request._owner, request._repo, request._pr_id)
        )
        if binaries:
            orphans = find_orphan_sources(
                binaries, fetch_source_info(self.apiurl, SOURCE_PROJECT), db
            )
        else:
            orphans = OrphanResult([], [], 0)

        if orphans.failed:
            self.logger.warning(
                "Binaries with no source package (not checked): %s",
                ", ".join(orphans.failed),
            )
        if orphans.orphans:
            self.logger.warning(
                "Orphan source packages (no maintainer): %s", ", ".join(orphans.orphans)
            )
        if users.not_found:
            self.logger.warning(
                "Maintainer logins not found in OBS: %s",
                ", ".join(sorted(users.not_found)),
            )
        if users.invalid:
            self.logger.warning(
                "Maintainer logins not confirmed: %s", ", ".join(sorted(users.invalid))
            )

        if orphans.orphans or users.invalid or users.not_found:
            self.review_messages["declined"] = build_declined_message(
                orphans.orphans, users.invalid, users.not_found
            )
            return False, None

        return True, build_accepted_message(orphans.failed)


class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.clazz = SleCheckBot

    def get_optparser(self):
        parser = super().get_optparser()

        parser.add_option(
            "--git-allow-repos",
            default=DEFAULT_ALLOW_REPOS,
            help="allowed git repositories (e.g. products/SLFO,products/SLES)",
        )

        return parser

    def setup_checker(self):
        instance = super().setup_checker()

        instance.allowed_repositories = [r for r in self.options.git_allow_repos.split(",") if r]

        return instance


if __name__ == "__main__":
    app = CommandLineInterface()
    logging.basicConfig(level=logging.INFO)

    sys.exit(app.main())
