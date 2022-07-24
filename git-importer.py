#!/usr/bin/env python3

import argparse
import datetime
import hashlib
import logging
import os
import pathlib
import sys
import time
import xml.etree.ElementTree as ET
from fnmatch import fnmatch
from urllib.error import HTTPError
from urllib.parse import quote

import osc.core
import pygit2
import requests

from osclib.cache import Cache

osc.conf.get_config(override_apiurl="https://api.opensuse.org")
# osc.conf.config['debug'] = True
apiurl = osc.conf.config["apiurl"]


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

Cache.init()
sha256s = {}


def fill_sha256(package):
    response = requests.get(f"http://source.dyn.cloud.suse.de/package/{package}")
    if response.status_code == 200:
        global sha256s
        sha256s = response.json()


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


def default_gitattributes():
    gattr = ["## Default LFS"] + [f"*{b} {LFS_SUFFIX}" for b in sorted(BINARY)]
    return "\n".join(gattr)


def is_binary(filename):
    # Shortcut the detection based on the file extension
    suffix = pathlib.Path(filename).suffix
    return suffix in BINARY


def md5(name):
    md5 = hashlib.md5()
    with open(name, "rb") as f:
        while True:
            chunk = f.read(1024 * 4)
            if not chunk:
                break
            md5.update(chunk)
    return md5.hexdigest()


repo = None
r = None


class Handler:
    def __init__(self, package) -> None:
        self.package = package
        self.projects = {}

    def get_revisions(self, project):
        revs = []
        u = osc.core.makeurl(apiurl, ["source", project, self.package, "_meta"])
        try:
            r = osc.core.http_GET(u)
            root = ET.parse(r).getroot()
            if root.get("project") != project:
                logging.debug("package does not live here")
                return revs
        except HTTPError:
            logging.debug("package has no meta!?")
            return revs

        u = osc.core.makeurl(apiurl, ["source", project, self.package, "_history"])
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logging.debug("package has no history!?")
            return revs

        root = ET.parse(r).getroot()
        for revision in root.findall("revision"):
            r = Revision(project, self.package).parse(revision)
            revs.append(r)

        self.projects[project] = revs
        return revs

    def find_lastrev(self, project, time):
        prev = None
        for rev in self.projects.get(project, []):
            if rev.time > time:
                return prev
            if rev.time == time:
                return rev
            prev = rev
        return prev

    def get_revision(self, project, revision):
        for r in self.projects.get(project, []):
            if str(r.rev) == revision:
                return r
            if r.srcmd5 == revision:
                return r
        # print(f"Can't find '{revision}' in {project}")
        # for r in self.projects.get(project, []):
        #    print(r)
        return None


class Revision:
    def __init__(self, project, package) -> None:
        self.project = project
        self.package = package
        self.commit = None
        self.broken = False

    def parse(self, xml):
        self.rev = int(xml.get("rev"))
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
        self.linkrev = None
        requestid = xml.find("requestid")
        if requestid is not None:
            self.requestid = int(requestid.text)
        else:
            self.requestid = None
        return self

    def __str__(self) -> str:
        return f"Rev {self.project}/{self.rev} Md5 {self.srcmd5} {self.time} {self.userid} {self.requestid}"

    def check_link(self, handler):
        u = osc.core.makeurl(
            apiurl,
            ["source", self.project, self.package, "_link"],
            {"rev": self.srcmd5},
        )
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            # no link
            return None
        try:
            root = ET.parse(r).getroot()
        except ET.XMLSyntaxError:
            logging.error(f"_link can't be parsed in {self}")
            self.broken = True
            return None
        tproject = root.get("project")
        rev = handler.find_lastrev(tproject, self.time)
        if rev:
            self.linkrev = rev.srcmd5

    # even if it's not a link we still need to check the expanded srcmd5 as it's possible used in
    # submit requests
    def check_expanded(self, handler):
        self.check_link(handler)
        opts = {"rev": self.srcmd5, "expand": "1"}
        if self.linkrev:
            opts["linkrev"] = self.linkrev
        u = osc.core.makeurl(apiurl, ["source", self.project, self.package], opts)
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logging.error(f"package can't be expanded in {self}")
            self.broken = True
            return None
        root = ET.parse(r).getroot()
        self.srcmd5 = root.get("srcmd5")

    def check_request(self, handler):
        if not self.requestid:
            return 0
        u = osc.core.makeurl(apiurl, ["request", str(self.requestid)])
        try:
            r = osc.core.http_GET(u)
        except HTTPError as e:
            print(u, e)
            return 0
        root = ET.parse(r)
        # print(ET.tostring(root).decode('utf-8'))
        # TODO: in projects other than Factory we
        # might find multi action requests
        if root.find("action").get("type") == "delete":
            return 0
        action_source = root.find("action/source")
        return action_source.get("rev") or 0

    def git_commit(self):
        index = repo.index
        index.add_all()
        index.write()
        author = pygit2.Signature(
            f"OBS User {r.userid}", "null@suse.de", time=int(self.time.timestamp())
        )
        commiter = pygit2.Signature(
            "Git OBS Bridge", "obsbridge@suse.de", time=int(self.time.timestamp())
        )
        message = r.comment + "\n\n" + str(self)
        ref = repo.head.name
        parents = [repo.head.target]

        tree = index.write_tree()
        self.commit = str(
            repo.create_commit(ref, author, commiter, message, tree, parents)
        )
        return self.commit

    def download(self, targetdir):
        if self.broken:
            return False
        try:
            root = ET.parse(
                osc.core.http_GET(
                    osc.core.makeurl(
                        apiurl,
                        ["source", self.project, self.package],
                        {"expand": 1, "rev": self.srcmd5},
                    )
                )
            )
        except HTTPError:
            return False
        newfiles = dict()
        files = dict()
        # caching this needs to consider switching branches
        for file in os.listdir(targetdir):
            if file == ".git":
                continue
            files[file] = md5(os.path.join(targetdir, file))

        # prepare default gitattributes
        target = os.path.join(targetdir, ".gitattributes")
        with open(target, "w") as f:
            f.write(default_gitattributes())
        first_non_default_lfs = True

        remotes = set()
        repo.index.read()
        different = False
        for entry in root.findall("entry"):
            name = entry.get("name")
            target = os.path.join(targetdir, name)
            size = int(entry.get("size"))
            large = size > 40000
            if large and (name.endswith(".changes") or name.endswith(".spec")):
                large = False
            fmd5 = entry.get("md5")
            if is_binary(name) or large:
                remotes.add(name)
                key = f"{fmd5}-{name}"
                if key not in sha256s:
                    quoted_name = quote(name)
                    url = f"{apiurl}/public/source/{self.project}/{self.package}/{quoted_name}?rev={self.srcmd5}"
                    response = requests.put(
                        "http://source.dyn.cloud.suse.de/",
                        data={"hash": fmd5, "filename": name, "url": url},
                    )
                    if response.status_code != 200:
                        print(response.content)
                        raise Exception("Redirector error on " + url + f" for {self}")
                    sha256s[key] = response.content.decode("utf-8")
                sha256 = sha256s[key]
                with open(target, "w") as f:
                    f.write("version https://git-lfs.github.com/spec/v1\n")
                    f.write(f"oid sha256:{sha256}\n")
                    f.write(f"size {size}\n")
                repo.index.add(name)
                remotes.add(name)
                newfiles[name] = md5(target)
                if newfiles[name] != files.pop(name, "none"):
                    different = True
                continue
            newfiles[name] = fmd5
            oldmd5 = files.pop(name, "none")
            if newfiles[name] != oldmd5:
                print("download", name)
                url = osc.core.makeurl(
                    apiurl,
                    ["source", self.project, self.package, quote(name)],
                    {"rev": self.srcmd5, "expand": "1"},
                )
                with open(target, "wb") as f:
                    f.write(osc.core.http_GET(url).read())
                if md5(target) != newfiles[name]:
                    raise Exception(f"Download error in {name}")
                repo.index.add(name)
                different = True
            files.pop(name, None)

        if remotes:
            target = os.path.join(targetdir, ".lfsconfig")
            with open(target, "w") as f:
                f.write("[lfs]\n  url = http://gitea.opensuse.org:9999/gitlfs")
            if ".lfsconfig" not in files:
                different = True
                repo.index.add(".lfsconfig")
            else:
                files.pop(".lfsconfig")

            # write .gitattributes
            target = os.path.join(targetdir, ".gitattributes")
            for file in sorted(remotes):
                # we differ between binaries and large files
                if not is_binary(file):
                    with open(target, "a") as f:
                        if first_non_default_lfs:
                            f.write("\n## Specific LFS patterns\n")
                        f.write(f"{file} {LFS_SUFFIX}\n")
                    first_non_default_lfs = False

        repo.index.add(".gitattributes")
        files.pop(".gitattributes", "none")

        for file in files:
            print("remove", file)
            repo.index.remove(file)
            os.unlink(os.path.join(targetdir, file))
            different = True
        return different


def get_devel_package(package):
    u = osc.core.makeurl(apiurl, ["source", "openSUSE:Factory", package, "_meta"])
    try:
        r = osc.core.http_GET(u)
    except HTTPError:
        # no link
        return None
    root = ET.parse(r).getroot()
    return root.find("devel").get("project")


def importer(package, repodir):
    global repo
    global r

    devel_project = get_devel_package(package)
    handler = Handler(package)
    revs_factory = handler.get_revisions("openSUSE:Factory")
    revs_devel = handler.get_revisions(devel_project)

    revs = sorted(revs_factory + revs_devel, key=lambda x: x.time.timestamp())

    os.mkdir(repodir)
    repo = pygit2.init_repository(repodir, False)

    index = repo.index
    index.write()
    author = pygit2.Signature(
        f"None", "null@suse.de", time=int(revs[0].time.timestamp())
    )
    commiter = pygit2.Signature(
        "Git OBS Bridge", "obsbridge@suse.de", time=int(revs[0].time.timestamp())
    )
    message = "Initialize empty repo"
    ref = "refs/heads/devel"
    parents = []

    tree = index.write_tree()
    empty_commit = repo.create_commit(ref, author, commiter, message, tree, parents)
    index = repo.index
    tree = index.write_tree()
    repo.create_branch("factory", repo.get(empty_commit))

    for r in revs:
        r.check_expanded(handler)
        if r.project == "openSUSE:Factory":
            branch = repo.lookup_branch("factory")
            ref = repo.lookup_reference(branch.name)
            repo.checkout(ref)
            submitted_revision = r.check_request(handler)
            if submitted_revision:
                rev = handler.get_revision(devel_project, submitted_revision)
                if not rev:
                    print(r)
                if rev and rev.commit:
                    author = pygit2.Signature(
                        f"OBS User {r.userid}",
                        "null@suse.de",
                        time=int(r.time.timestamp()),
                    )
                    commiter = pygit2.Signature(
                        "Git OBS Bridge",
                        "obsbridge@suse.de",
                        time=int(r.time.timestamp()),
                    )
                    message = f"Accepting request {r.requestid}: {r.comment}"
                    message += "\n\n" + str(r)
                    print("merge request", rev.commit)
                    mr = repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)
                    if repo.index.conflicts:
                        message = "CONFLICT " + message
                        for conflict in repo.index.conflicts:
                            print("CONFLICT", conflict)
                        for path, mode in repo.status().items():
                            # merge leaves ~HEAD and ~REV files behind
                            if mode == pygit2.GIT_STATUS_WT_NEW:
                                logging.debug(f"Remove {path}")
                                os.unlink(os.path.join(repodir, path))
                            if mode == pygit2.GIT_STATUS_CONFLICTED:
                                # remove files in conflict - we'll download the revision
                                # TODO: just as in above, if we have a conflict, the commit isn't fitting
                                try:
                                    os.unlink(os.path.join(repodir, path))
                                except FileNotFoundError:
                                    pass
                                try:
                                    repo.index.remove(path)
                                except OSError:
                                    pass
                        print(repo.status())

                    r.download(repodir)
                    index = repo.index
                    index.add_all()
                    index.write()
                    tree = index.write_tree()
                    parents = [
                        repo.head.target,
                        repo.get(rev.commit).peel(pygit2.Commit).id,
                    ]
                    print("create", parents)
                    r.commit = repo.create_commit(
                        repo.head.name, author, commiter, message, tree, parents
                    )
                    repo.references["refs/heads/devel"].set_target(r.commit)
                    continue
                else:
                    logging.warning(str(rev) + " submitted from another devel project")
        else:
            branch = repo.lookup_branch("devel")
            ref = repo.lookup_reference(branch.name)
            repo.checkout(ref)

        if not r.download(repodir) and r.userid == "buildservice-autocommit":
            continue
        print("commit", r.project, r.rev)
        r.git_commit()

    osc.conf.get_config(override_apiurl="https://api.suse.de")
    apiurl = osc.conf.config["apiurl"]
    # conf.config['debug'] = True

    first = dict()
    projects = [
        ("SUSE:SLE-12:GA", "SLE_12"),
        ("SUSE:SLE-12:Update", "SLE_12"),
        ("SUSE:SLE-12-SP1:GA", "SLE_12_SP1"),
        ("SUSE:SLE-12-SP1:Update", "SLE_12_SP1"),
        ("SUSE:SLE-12-SP2:GA", "SLE_12_SP2"),
        ("SUSE:SLE-12-SP2:Update", "SLE_12_SP2"),
        ("SUSE:SLE-12-SP3:GA", "SLE_12_SP3"),
        ("SUSE:SLE-12-SP3:Update", "SLE_12_SP3"),
        ("SUSE:SLE-12-SP4:GA", "SLE_12_SP4"),
        ("SUSE:SLE-12-SP4:Update", "SLE_12_SP4"),
        ("SUSE:SLE-12-SP5:GA", "SLE_12_SP5"),
        ("SUSE:SLE-12-SP5:Update", "SLE_12_SP5"),
        ("SUSE:SLE-15:GA", "SLE_15"),
        ("SUSE:SLE-15:Update", "SLE_15"),
        ("SUSE:SLE-15-SP1:GA", "SLE_15_SP1"),
        ("SUSE:SLE-15-SP1:Update", "SLE_15_SP1"),
        ("SUSE:SLE-15-SP2:GA", "SLE_15_SP2"),
        ("SUSE:SLE-15-SP2:Update", "SLE_15_SP2"),
        ("SUSE:SLE-15-SP3:GA", "SLE_15_SP3"),
        ("SUSE:SLE-15-SP3:Update", "SLE_15_SP3"),
        ("SUSE:SLE-15-SP4:GA", "SLE_15_SP4"),
        ("SUSE:SLE-15-SP4:Update", "SLE_15_SP4"),
    ]
    for project, branchname in projects:
        revs = handler.get_revisions(project)
        for r in revs:
            r.check_expanded(handler)
            rev = handler.get_revision("openSUSE:Factory", r.srcmd5)
            if first.get(branchname, True):
                index = repo.index
                tree = index.write_tree()
                base_commit = None
                if rev and rev.commit:
                    base_commit = rev.commit
                if not base_commit:
                    # try older SLE versions
                    oprojects = []
                    obranches = []
                    for oproject, obranchname in projects:
                        if obranchname == branchname:
                            break
                        oprojects.append(oproject)
                        obranches.append(obranchname)
                    logging.debug(f"looking for {r.srcmd5}")
                    for oproject in reversed(oprojects):
                        logging.debug(f"looking for {r.srcmd5} in {oproject}")
                        rev = handler.get_revision(oproject, r.srcmd5)
                        if rev:
                            logging.debug(f"found {r.srcmd5} in {oproject}: {rev}")
                            base_commit = rev.commit
                            break
                    if not base_commit:
                        min_patch_size = sys.maxsize
                        min_commit = None

                        # create temporary commit to diff it
                        logging.debug(f"Create tmp commit for {r}")
                        repo.create_branch("tmp", repo.get(empty_commit))
                        branch = repo.lookup_branch("tmp")
                        ref = repo.lookup_reference(branch.name)
                        repo.checkout(ref)
                        r.download(repodir)
                        repo.index.write()
                        tree = repo.index.write_tree()
                        parent, ref = repo.resolve_refish(refish=repo.head.name)
                        new_commit = repo.create_commit(
                            ref.name,
                            repo.default_signature,
                            repo.default_signature,
                            "Temporary branch",
                            tree,
                            [parent.oid],
                        )
                        logging.debug(f"Created tmp commit for {new_commit}")
                        obranches.append("factory")
                        obranches.append("devel")
                        for obranch in obranches:
                            branch = repo.lookup_branch(obranch)
                            # TODO we need to create a branch even if there are no revisions in a SP
                            if not branch:
                                continue
                            ref = repo.get(branch.target).peel(pygit2.Commit).id

                            for commit in repo.walk(ref, pygit2.GIT_SORT_TIME):
                                d = repo.diff(new_commit, commit)
                                patch_len = len(d.patch)
                                # print(f"diff between {commit} and {new_commit} is {patch_len}")
                                if min_patch_size > patch_len:
                                    min_patch_size = patch_len
                                    min_commit = commit
                        if min_patch_size < 1000:
                            base_commit = min_commit.id
                            logging.debug(f"Base {r} on {base_commit}")
                        else:
                            logging.debug(f"Min patch is {min_patch_size} - ignoring")
                        branch = repo.lookup_branch("factory")
                        ref = repo.lookup_reference(branch.name)
                        repo.reset(ref.peel().id, pygit2.GIT_RESET_HARD)
                        repo.checkout(ref)
                        repo.branches.delete("tmp")
                if base_commit:
                    repo.create_branch(branchname, repo.get(base_commit))
                else:
                    author = pygit2.Signature(
                        f"No one", "null@suse.de", time=int(r.time.timestamp())
                    )
                    commiter = pygit2.Signature(
                        "Git OBS Bridge",
                        "obsbridge@suse.de",
                        time=int(r.time.timestamp()),
                    )
                    repo.create_commit(
                        f"refs/heads/{branchname}",
                        author,
                        commiter,
                        "Initialize branch",
                        tree,
                        [],
                    )

                first[branchname] = False
                branch = repo.lookup_branch(branchname)
                ref = repo.lookup_reference(branch.name)
                repo.checkout(ref)
            elif rev and rev.commit:
                author = pygit2.Signature(
                    f"OBS User {r.userid}", "null@suse.de", time=int(r.time.timestamp())
                )
                commiter = pygit2.Signature(
                    "Git OBS Bridge", "obsbridge@suse.de", time=int(r.time.timestamp())
                )
                message = r.comment or "No commit log found"
                message += "\n\n" + str(r)
                print("merge", rev.commit)
                mr = repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)
                if repo.index.conflicts:
                    message = "CONFLICT " + message
                    # TODO we really should not run into conflicts. but for that we need to be aware of the
                    # order the commits happen other than what the time says
                    for conflict in repo.index.conflicts:
                        logging.warning(f"CONFLICT #{conflict}")
                    for path, mode in repo.status().items():
                        # merge leaves ~HEAD and ~REV files behind
                        if mode == pygit2.GIT_STATUS_WT_NEW:
                            logging.debug(f"Remove {path}")
                            os.unlink(os.path.join(repodir, path))
                        if mode == pygit2.GIT_STATUS_CONFLICTED:
                            # remove files in conflict - we'll download the revision
                            # TODO: just as in above, if we have a conflict, the commit isn't fitting
                            try:
                                os.unlink(os.path.join(repodir, path))
                            except FileNotFoundError:
                                pass
                            try:
                                repo.index.remove(path)
                            except OSError:
                                pass
                    print(repo.status())

                r.download(repodir)
                index = repo.index
                index.add_all()
                index.write()
                tree = index.write_tree()
                parents = [
                    repo.head.target,
                    repo.get(rev.commit).peel(pygit2.Commit).id,
                ]
                print("create", parents)
                r.commit = repo.create_commit(
                    repo.head.name, author, commiter, message, tree, parents
                )
                # repo.references["refs/heads/SLE_15_GA"].set_target(r.commit)
                continue

            print("commit", r.project, r.rev)
            r.download(repodir)
            r.git_commit()


def main():
    parser = argparse.ArgumentParser(description="OBS history importer into git")
    parser.add_argument("package", help="OBS package name")
    parser.add_argument(
        "-r", "--repodir", required=False, help="Local git repository directory"
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

    if not args.repodir:
        args.repodir = args.package

    fill_sha256(args.package)

    importer(args.package, args.repodir)


if __name__ == "__main__":
    main()
