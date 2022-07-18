#! /usr/bin/python3

from tempfile import TemporaryFile
import osc.core
import logging
from urllib.error import HTTPError
from lxml import etree as ET
import datetime
import os
import pygit2
import sys
import pathlib
import hashlib
import requests
from urllib.parse import quote
from osclib.cache import Cache
from osc import conf

osc.conf.get_config(override_apiurl='https://api.opensuse.org')
#conf.config['debug'] = True
apiurl = osc.conf.config['apiurl']

logger = logging.getLogger("Importer")
logging.basicConfig()
logger.setLevel(logging.DEBUG)

# copied from obsgit
BINARY = {
    ".xz",
    ".gz",
    ".bz2",
    ".zip",
    ".gem",
    ".tgz",
    ".png",
    ".pdf",
    ".jar",
    ".oxt",
    ".whl",
    ".rpm",
    ".obscpio"
}

Cache.init()
sha256s = dict()

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


class Handler:
    def __init__(self, package) -> None:
        self.package = package
        self.projects = dict()

    def get_revisions(self, project):
        revs = []
        u = osc.core.makeurl(apiurl, ['source', project, self.package, '_meta'])
        try:
            r = osc.core.http_GET(u)
            root = ET.parse(r).getroot()
            if root.get('project') != project:
                logger.debug("package does not live here")
                return revs
        except HTTPError:
            logger.debug("package has no meta!?")
            return revs

        u = osc.core.makeurl(apiurl, ['source', project, self.package, '_history'])
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logger.debug("package has no history!?")
            return revs

        root = ET.parse(r).getroot()
        for revision in root.findall('revision'):
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
        #print(f"Can't find '{revision}' in {project}")
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
        self.rev = int(xml.get('rev'))
        self.srcmd5 = xml.find('srcmd5').text
        self.version = xml.find('version').text
        time = int(xml.find('time').text)
        self.time = datetime.datetime.fromtimestamp(time)
        userid = xml.find('user')
        if userid is not None:
            self.userid = userid.text
        else:
            self.userid = 'unknown'
        comment = xml.find('comment')
        if comment is not None:
            self.comment = comment.text or ''
        else:
            self.comment = ''
        self.linkrev = None
        requestid = xml.find('requestid')
        if requestid is not None:
            self.requestid = int(requestid.text)
        else:
            self.requestid = None
        return self

    def __str__(self) -> str:
        return f"Rev {self.project}/{self.rev} Md5 {self.srcmd5} {self.time} {self.userid} {self.requestid}"

    def check_link(self, handler):
        u = osc.core.makeurl(apiurl, ['source', self.project, self.package, '_link'], {'rev': self.srcmd5})
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            # no link
            return None
        try:
            root = ET.parse(r).getroot()
        except ET.XMLSyntaxError:
            logger.error(f"_link can't be parsed in {self}")
            self.broken = True
            return None
        tproject = root.get('project')
        rev = handler.find_lastrev(tproject, self.time)
        if rev:
            self.linkrev = rev.srcmd5

    # even if it's not a link we still need to check the expanded srcmd5 as it's possible used in
    # submit requests
    def check_expanded(self, handler):
        self.check_link(handler)
        opts = {'rev': self.srcmd5, 'expand': '1'}
        if self.linkrev:
            opts['linkrev'] = self.linkrev
        u = osc.core.makeurl(apiurl, ['source', self.project, self.package], opts)
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logger.error(f"package can't be expanded in {self}")
            self.broken = True
            return None
        root = ET.parse(r).getroot()
        self.srcmd5 = root.get('srcmd5')

    def check_request(self):
        if not self.requestid:
            return 0
        u = osc.core.makeurl(apiurl, ['request', str(self.requestid)])
        try:
            r = osc.core.http_GET(u)
        except HTTPError as e:
            print(u, e)
            return 0
        root = ET.parse(r)
        # print(ET.tostring(root).decode('utf-8'))
        # TODO: this only works for Factory
        action_source = root.find('action/source')
        return action_source.get('rev') or 0

    def git_commit(self):
        index = repo.index
        index.add_all()
        index.write()
        author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de', time=int(self.time.timestamp()))
        commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(self.time.timestamp()))
        message = r.comment + "\n\n" + str(self)
        ref = repo.head.name
        parents = [repo.head.target]

        tree = index.write_tree()
        self.commit = str(repo.create_commit(ref, author, commiter, message, tree, parents))
        return self.commit

    def download(self, targetdir):
        if self.broken:
            return False
        try:
            root = ET.parse(osc.core.http_GET(osc.core.makeurl(
                apiurl, ['source', self.project, self.package], {'expand': 1, 'rev': self.srcmd5})))
        except HTTPError:
            return False
        newfiles = dict()
        files = dict()
        # caching this needs to consider switching branches
        for file in os.listdir(targetdir):
            if file == '.git':
                continue
            files[file] = md5(os.path.join(targetdir, file))
        remotes = dict()
        repo.index.read()
        different = False
        for entry in root.findall('entry'):
            name = entry.get('name')
            if not (name.endswith('.spec') or name.endswith('.changes')):
                pass
                #continue
            size = int(entry.get('size'))
            large = size > 40000
            if large and (name.endswith('.changes') or name.endswith('.spec')):
                large = False
            fmd5 = entry.get('md5')
            if is_binary(name) or large:
                remotes[name] = fmd5
                key = f'{fmd5}-{name}'
                if key not in sha256s:
                    quoted_name = quote(name)
                    url = f'{apiurl}/public/source/{self.project}/{self.package}/{quoted_name}?rev={self.srcmd5}'
                    response = requests.put('http://source.dyn.cloud.suse.de/',
                                data={'hash': fmd5, 'filename': name, 'url': url})
                    if response.status_code != 200:
                        print(response.content)
                        raise Exception("Redirector error on " + url + f" for {self}")
                    sha256s[key] = response.content.decode('utf-8')
                sha256 = sha256s[key]
                with open(os.path.join(targetdir, name), 'w') as f:
                    f.write("version https://git-lfs.github.com/spec/v1\n")
                    f.write(f"oid sha256:{sha256}\n")
                    f.write(f"size {size}\n")
                repo.index.add(name)
                continue
            newfiles[name] = fmd5
            oldmd5 = files.pop(name, 'none')
            if newfiles[name] != oldmd5:
                print('download', name)
                url = osc.core.makeurl(apiurl, [
                    'source', self.project, self.package, quote(name)], {'rev': self.srcmd5, 'expand': '1'})
                target = os.path.join(targetdir, name)
                with open(target, 'wb') as f:
                    f.write(osc.core.http_GET(url).read())
                if md5(target) != newfiles[name]:
                    raise Exception(f'Download error in {name}')
                repo.index.add(name)
                different = True
            files.pop(name, None)
        for file in files:
            print('remove', file)
            repo.index.remove(file)
            os.unlink(os.path.join(targetdir, file))
            different = True
        if len(remotes):
            # TODO add tracking files
            pass
        return different


def get_devel_package(package):
    u = osc.core.makeurl(apiurl, ['source', 'openSUSE:Factory', package, '_meta'])
    try:
        r = osc.core.http_GET(u)
    except HTTPError:
        # no link
        return None
    root = ET.parse(r).getroot()
    return root.find('devel').get('project')


package = sys.argv[1]
repodir = sys.argv[2]
devel_project = get_devel_package(package)
handler = Handler(package)
revs_factory = handler.get_revisions('openSUSE:Factory')
revs_devel = handler.get_revisions(devel_project)

revs = sorted(revs_factory + revs_devel, key=lambda x: x.time.timestamp())

os.mkdir(repodir)
repo = pygit2.init_repository(repodir, False)

index = repo.index
index.write()
author = pygit2.Signature(f'None', 'null@suse.de', time=int(revs[0].time.timestamp()))
commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(revs[0].time.timestamp()))
message = 'Initialize empty repo'
ref = 'refs/heads/devel'
parents = []

tree = index.write_tree()
empty_commit = repo.create_commit(ref, author, commiter, message, tree, parents)
index = repo.index
tree = index.write_tree()
repo.create_branch('factory', repo.get(empty_commit))

for r in revs:
    r.check_expanded(handler)
    if r.project == 'openSUSE:Factory':
        branch = repo.lookup_branch('factory')
        ref = repo.lookup_reference(branch.name)
        repo.checkout(ref)
        submitted_revision = r.check_request()
        if submitted_revision:
            rev = handler.get_revision(devel_project, submitted_revision)
            if not rev:
                print(r)
            if rev and rev.commit:
                author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de', time=int(r.time.timestamp()))
                commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(r.time.timestamp()))
                message = r.comment or f'Accepting request {r.requestid}'
                print('merge request', rev.commit)
                mr = repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)
                if repo.index.conflicts:
                    for conflict in repo.index.conflicts:
                        print('CONFLICT', conflict)
                    for path, mode in repo.status().items():
                        # merge leaves ~HEAD and ~REV files behind
                        if mode == pygit2.GIT_STATUS_WT_NEW:
                            logger.debug(f"Remove {path}")
                            os.unlink(os.path.join(repodir, path))
                        if mode == pygit2.GIT_STATUS_CONFLICTED:
                            # remove files in conflict - we'll download the revision
                            # TODO: just as in above, if we have a conflict, the commit isn't fitting
                            os.unlink(os.path.join(repodir, path))
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
                parents = [repo.head.target, repo.get(rev.commit).peel(pygit2.Commit).id]
                print("create", parents)
                r.commit = repo.create_commit(repo.head.name, author, commiter, message, tree, parents)
                repo.references["refs/heads/devel"].set_target(r.commit)
                continue
            else:
                logger.warning(str(rev) + " submitted from another devel project")
    else:
        branch = repo.lookup_branch('devel')
        ref = repo.lookup_reference(branch.name)
        repo.checkout(ref)

    if not r.download(repodir) and r.userid == 'buildservice-autocommit':
        continue
    print("commit", r.project, r.rev)
    r.git_commit()

osc.conf.get_config(override_apiurl='https://api.suse.de')
apiurl = osc.conf.config['apiurl']
#conf.config['debug'] = True

first = dict()
projects = [('SUSE:SLE-12:GA', 'SLE_12'), ('SUSE:SLE-12:Update', 'SLE_12'),
            ('SUSE:SLE-12-SP1:GA', 'SLE_12_SP1'), ('SUSE:SLE-12-SP1:Update', 'SLE_12_SP1'),
            ('SUSE:SLE-12-SP2:GA', 'SLE_12_SP2'), ('SUSE:SLE-12-SP2:Update', 'SLE_12_SP2'),
            ('SUSE:SLE-12-SP3:GA', 'SLE_12_SP3'), ('SUSE:SLE-12-SP3:Update', 'SLE_12_SP3'),
            ('SUSE:SLE-12-SP4:GA', 'SLE_12_SP4'), ('SUSE:SLE-12-SP4:Update', 'SLE_12_SP4'),
            ('SUSE:SLE-12-SP5:GA', 'SLE_12_SP5'), ('SUSE:SLE-12-SP5:Update', 'SLE_12_SP5'),
            ('SUSE:SLE-15:GA', 'SLE_15'), ('SUSE:SLE-15:Update', 'SLE_15'),
            ('SUSE:SLE-15-SP1:GA', 'SLE_15_SP1'), ('SUSE:SLE-15-SP1:Update', 'SLE_15_SP1'),
            ('SUSE:SLE-15-SP2:GA', 'SLE_15_SP2'), ('SUSE:SLE-15-SP2:Update', 'SLE_15_SP2'),
            ('SUSE:SLE-15-SP3:GA', 'SLE_15_SP3'), ('SUSE:SLE-15-SP3:Update', 'SLE_15_SP3'),
            ('SUSE:SLE-15-SP4:GA', 'SLE_15_SP4'), ('SUSE:SLE-15-SP4:Update', 'SLE_15_SP4')
            ]
for project, branchname in projects:
    revs = handler.get_revisions(project)
    for r in revs:
        r.check_expanded(handler)
        rev = handler.get_revision('openSUSE:Factory', r.srcmd5)
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
                logger.debug(f"looking for {r.srcmd5}")
                for oproject in reversed(oprojects):
                    logger.debug(f"looking for {r.srcmd5} in {oproject}")
                    rev = handler.get_revision(oproject, r.srcmd5)
                    if rev:
                        logger.debug(f"found {r.srcmd5} in {oproject}: {rev}")
                        base_commit = rev.commit
                        break
                if not base_commit:
                    min_patch_size = sys.maxsize
                    min_commit = None

                    # create temporary commit to diff it
                    logger.debug(f"Create tmp commit for {r}")
                    repo.create_branch('tmp', repo.get(empty_commit))
                    branch = repo.lookup_branch('tmp')
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
                    logger.debug(f"Created tmp commit for {new_commit}")
                    obranches.append('factory')
                    obranches.append('devel')
                    for obranch in obranches:
                        branch = repo.lookup_branch(obranch)
                        # TODO we need to create a branch even if there are no revisions in a SP
                        if not branch:
                            continue
                        ref = repo.get(branch.target).peel(pygit2.Commit).id

                        for commit in repo.walk(ref, pygit2.GIT_SORT_TIME):
                            d = repo.diff(new_commit, commit)
                            patch_len = len(d.patch)
                            #print(f"diff between {commit} and {new_commit} is {patch_len}")
                            if min_patch_size > patch_len:
                                min_patch_size = patch_len
                                min_commit = commit
                    if min_patch_size < 1000:
                        base_commit = min_commit.id
                        logger.debug(f"Base {r} on {base_commit}")
                    else:
                        logger.debug(f"Min patch is {min_patch_size} - ignoring")
                    branch = repo.lookup_branch('factory')
                    ref = repo.lookup_reference(branch.name)
                    repo.reset(ref.peel().id, pygit2.GIT_RESET_HARD)
                    repo.checkout(ref)
                    repo.branches.delete('tmp')
            if base_commit:
                repo.create_branch(branchname, repo.get(base_commit))
            else:
                author = pygit2.Signature(f'No one', 'null@suse.de', time=int(r.time.timestamp()))
                commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(r.time.timestamp()))
                repo.create_commit(
                    f'refs/heads/{branchname}',
                    author,
                    commiter,
                    "Initialize branch",
                    tree,
                    [])

            first[branchname] = False
            branch = repo.lookup_branch(branchname)
            ref = repo.lookup_reference(branch.name)
            repo.checkout(ref)
        elif rev and rev.commit:
            author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de', time=int(r.time.timestamp()))
            commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(r.time.timestamp()))
            message = r.comment or 'No commit log found'
            print('merge', rev.commit)
            mr = repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)
            if repo.index.conflicts:
                # TODO we really should not run into conflicts. but for that we need to be aware of the
                # order the commits happen other than what the time says
                for conflict in repo.index.conflicts:
                    logger.warning(f'CONFLICT #{conflict}')
                for path, mode in repo.status().items():
                    # merge leaves ~HEAD and ~REV files behind
                    if mode == pygit2.GIT_STATUS_WT_NEW:
                        logger.debug(f"Remove {path}")
                        os.unlink(os.path.join(repodir, path))
                    if mode == pygit2.GIT_STATUS_CONFLICTED:
                        # remove files in conflict - we'll download the revision
                        # TODO: just as in above, if we have a conflict, the commit isn't fitting
                        os.unlink(os.path.join(repodir, path))
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
            parents = [repo.head.target, repo.get(rev.commit).peel(pygit2.Commit).id]
            print("create", parents)
            r.commit = repo.create_commit(repo.head.name, author, commiter, message, tree, parents)
            # repo.references["refs/heads/SLE_15_GA"].set_target(r.commit)
            continue

        print("commit", r.project, r.rev)
        r.download(repodir)
        r.git_commit()
