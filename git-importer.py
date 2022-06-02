#! /usr/bin/python3

import osc.core
import logging
from urllib.error import HTTPError, URLError
from lxml import etree as ET
import datetime
import os
import pygit2

logger = logging.getLogger()
osc.conf.get_config(override_apiurl='https://api.opensuse.org')
apiurl = osc.conf.config['apiurl']

class Handler:
    def __init__(self, package) -> None:
        self.package = package
        self.projects = dict()

    def get_revisions(self, project):
        revs = []
        u = osc.core.makeurl(apiurl, ['source', project, self.package, '_history'])
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logger.debug("package has no history!?")
            return None

        root = ET.parse(r).getroot()
        for revision in root.findall('revision'):
            r = Revision(project, self.package).parse(revision)
            #if r.userid == 'buildservice-autocommit':
            #    continue
            revs.append(r)

        self.projects[project] = revs
        return revs

    def find_lastrev(self, project, time):
        prev = None
        for rev in self.projects[project]:
            if rev.time > time:
                return prev
            if rev.time == time:
                return rev
            prev = rev
        return prev

    def get_revision(self, project, revision):
        for r in self.projects[project]:
            if str(r.rev) == revision:
                return r
            if r.srcmd5 == revision:
                return r
        print(f"Can't find {revision} in {project}")
        return None

class Revision:
    def __init__(self, project, package) -> None:
        self.project = project
        self.package = package

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
        return f"Rev {self.rev} Md5 {self.srcmd5} {self.time}"

    def check_link(self, handler):
        u = osc.core.makeurl(apiurl, ['source', self.project, self.package, '_link'], {'rev': self.srcmd5})
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            # no link
            return None
        root = ET.parse(r).getroot()
        tproject = root.get('project')
        self.linkrev = handler.find_lastrev(tproject, self.time).srcmd5

        u = osc.core.makeurl(apiurl, ['source', self.project, self.package], {'rev': self.srcmd5, 'linkrev': self.linkrev, 'expand': '1'})
        try:
            r = osc.core.http_GET(u)
        except HTTPError:
            logger.error("package can't be expanded")
            return None
        root = ET.parse(r).getroot()
        self.srcmd5 = root.get('srcmd5')

    def get_file(self, file, target):
        opts = {'rev': self.srcmd5, 'expand': 1}
        u = osc.core.makeurl(apiurl, ['source', self.project, self.package, file], opts)
        try:
            r = osc.core.http_GET(u)
        except HTTPError as e:
            print(u, e)
            return None
        with open(target, 'wb') as f:
            f.write(r.read())

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
        #print(ET.tostring(root).decode('utf-8'))
        # TODO: this only works for Factory
        action_source = root.find('action/source')
        return action_source.get('rev') or 0

    def git_commit(self):
        index = repo.index
        index.add_all()
        index.write()
        author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de', time=int(self.time.timestamp()))
        commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de')
        message = r.comment
        ref = repo.head.name
        parents = [repo.head.target]

        tree = index.write_tree()
        self.commit = str(repo.create_commit(ref, author, commiter, message, tree, parents))
        return self.commit

handler = Handler('bash')
revs_factory = handler.get_revisions('openSUSE:Factory')
revs_devel = handler.get_revisions('Base:System')

os.mkdir('repo')
repo = pygit2.init_repository('repo', False)
last_commit = None

index = repo.index
index.write()
author = pygit2.Signature(f'None', 'null@suse.de', time=int(revs_devel[0].time.timestamp()))
commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de')
message = 'Initialize empty repo'
ref = 'refs/heads/devel'
parents = []

tree = index.write_tree()
repo.create_commit(ref, author, commiter, message, tree, parents)
branch = repo.lookup_branch('devel')
ref = repo.lookup_reference(branch.name)
repo.reset(ref.peel().id, pygit2.GIT_RESET_HARD)
repo.checkout(ref)

for r in revs_devel:
    r.check_link(handler)
    r.get_file('bash.spec', 'repo/bash.spec')
    r.git_commit()

index = repo.index
tree = index.write_tree()
repo.create_branch('factory', repo.get(handler.get_revision('Base:System', '1').commit))
branch = repo.lookup_branch('factory')
ref = repo.lookup_reference(branch.name)
repo.checkout(ref)

last_commit = ref
for r in revs_factory:
    submitted_revision = r.check_request()
    if submitted_revision:
        rev = handler.get_revision('Base:System', submitted_revision)
        author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de')
        commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de')
        message = r.comment or f'Accepting request {r.requestid}'
        ref = repo.head.name
        repo.reset(repo.head.target, pygit2.GIT_RESET_HARD)
        repo.checkout(ref)
        repo.merge(repo.get(rev.commit).peel(pygit2.Commit).id)
  
        r.get_file('bash.spec', 'repo/bash.spec')
        index.add_all()
        index.write()
        parents = [repo.head.target, repo.get(rev.commit).peel(pygit2.Commit).id]
        print("create", parents)
        commit = repo.create_commit(ref, author, commiter, message, tree, parents)
        last_commit = commit
    else:
        print("commit")
        r.get_file('bash.spec', 'repo/bash.spec')
        last_commit = r.git_commit()
