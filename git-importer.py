#! /usr/bin/python3

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
from osc.core import quote_plus

logger = logging.getLogger()
osc.conf.get_config(override_apiurl='https://api.opensuse.org')
apiurl = osc.conf.config['apiurl']

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
            return None

        u = osc.core.makeurl(apiurl, ['source', project, self.package, '_history'])
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logger.debug("package has no history!?")
            return None

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
        print(f"Can't find '{revision}' in {project}")
        for r in self.projects.get(project, []):
            print(r)
        return None


class Revision:
    def __init__(self, project, package) -> None:
        self.project = project
        self.package = package
        self.commit = None

    def parse(self, xml):
        self.rev = int(xml.get('rev'))
        self.vrev = int(xml.get('vrev'))
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
        root = ET.parse(r).getroot()
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
            logger.error("package can't be expanded")
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
            if not name.endswith('.spec'):
                continue
            large = int(entry.get('size')) > 40000
            if large and (name.endswith('.changes') or name.endswith('.spec')):
                large = False
            if is_binary(name) or large:
                remotes[name] = entry.get('md5')
                quoted_name = quote_plus(name)
                url = f'{apiurl}/public/source/{self.project}/{self.package}/{quoted_name}?rev={self.srcmd5}'
                requests.put('http://source.dyn.cloud.suse.de/',
                             data={'hash': entry.get('md5'), 'filename': name, 'url': url})
                continue
            newfiles[name] = entry.get('md5')
            oldmd5 = files.pop(name, 'none')
            if newfiles[name] != oldmd5:
                print('download', name)
                url = osc.core.makeurl(apiurl, [
                    'source', self.project, self.package, quote_plus(name)], {'rev': self.srcmd5})
                target = os.path.join(targetdir, name)
                with open(target, 'wb') as f:
                    f.write(osc.core.http_GET(url).read())
                if md5(target) != newfiles[name]:
                    raise Exception(f'Download error in {name}')
                repo.index.add(name)
                different = True
            files.pop(name, None)
        for file in files:
            if file == '_remoteassets':
                continue
            print('remove', file)
            repo.index.remove(file)
            os.unlink(os.path.join(targetdir, file))
            different = True
        if len(remotes):
            with open(os.path.join(targetdir, '_remoteassets'), 'wb') as f:
                for file in sorted(remotes):
                    quoted_file = quote_plus(file)
                    f.write(
                        f'#!RemoteAssetURL: http://source.dyn.cloud.suse.de/{remotes[file]}/{quoted_file}\n'.encode('utf-8'))
            repo.index.add('_remoteassets')
            if files.get('_remoteassets') != md5(os.path.join(targetdir, '_remoteassets')):
                different = True
        elif '_remoteassets' in files:
            repo.index.remove('_remoteassets')
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


package = 'Mesa'
devel_project = get_devel_package(package)
handler = Handler(package)
revs_factory = handler.get_revisions('openSUSE:Factory')
revs_devel = handler.get_revisions(devel_project)

for r in revs_devel:
    r.check_expanded(handler)

revs = sorted(revs_factory + revs_devel, key=lambda x: x.time.timestamp())

os.mkdir('repo')
repo = pygit2.init_repository('repo', False)

index = repo.index
index.write()
author = pygit2.Signature(f'None', 'null@suse.de', time=int(revs[0].time.timestamp()))
commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(revs[0].time.timestamp()))
message = 'Initialize empty repo'
ref = 'refs/heads/devel'
parents = []

tree = index.write_tree()
commit = repo.create_commit(ref, author, commiter, message, tree, parents)

index = repo.index
tree = index.write_tree()
repo.create_branch('factory', repo.get(commit))

for r in revs:
    if r.project == 'openSUSE:Factory':
        branch = repo.lookup_branch('factory')
        ref = repo.lookup_reference(branch.name)
        repo.checkout(ref)
        submitted_revision = r.check_request()
        if submitted_revision:
            rev = handler.get_revision(devel_project, submitted_revision)
            if not rev:
                print(r)
            if rev.commit:
                author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de', time=int(r.time.timestamp()))
                commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de', time=int(r.time.timestamp()))
                message = r.comment or f'Accepting request {r.requestid}'
                repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)

                r.download('repo')
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

    if not r.download('repo') and r.userid == 'buildservice-autocommit':
        continue
    print("commit", r.project, r.rev)
    r.git_commit()

osc.conf.get_config(override_apiurl='https://api.suse.de')
apiurl = osc.conf.config['apiurl']

first = dict()
projects = [('SUSE:SLE-15:GA', 'SLE_15'), ('SUSE:SLE-15:Update', 'SLE_15'),
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
            if not rev:
                # try older SLE versions
                oprojects = []
                for oproject, obranchname in projects:
                    if obranchname == branchname:
                        break
                    oprojects.append(oproject)
                for oproject in reversed(oprojects):
                    rev = handler.get_revision(oproject, r.srcmd5)
                    if rev:
                        break
            if rev and rev.commit:
                repo.create_branch(branchname, repo.get(rev.commit))
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
            repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)

            r.download('repo')
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
        r.download('repo')
        r.git_commit()
