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
            revs.append(r)

        self.projects[project] = revs
        return revs

    def find_lastrev(self, project, time):
        prev = None
        for rev in self.projects[project]:
            if rev.time >= time:
                return prev
            prev = rev

class Revision:
    def __init__(self, project, package) -> None:
        self.project = project
        self.package = package

    def parse(self, xml):
        self.rev = xml.get('rev')
        self.vrev = xml.get('vrev')
        self.srcmd5 = xml.find('srcmd5').text
        self.version = xml.find('version').text
        time = int(xml.find('time').text)
        self.time = datetime.datetime.fromtimestamp(time)
        userid = xml.find('userid')
        if userid is not None:
            self.userid = userid.text
        else:
            self.userid = 'unknown'
        comment = xml.find('comment')
        if comment is not None:
            self.comment = comment.text
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
            return
        u = osc.core.makeurl(apiurl, ['request', str(self.requestid)])
        try:
            r = osc.core.http_GET(u)
        except HTTPError as e:
            print(u, e)
            return None
        root = ET.parse(r)
        print(ET.tostring(root).decode('utf-8'))

handler = Handler('bash')
revs_factory = handler.get_revisions('openSUSE:Factory')
revs_devel = handler.get_revisions('Base:System')

os.mkdir('repo')
repo = pygit2.init_repository('repo', False)
first_commit = True

for r in revs_devel:
    r.check_link(handler)
    r.get_file('bash.spec', 'repo/bash.spec')
    index = repo.index
    index.add_all()
    index.write()
    author = pygit2.Signature(f'OBS User {r.userid}', 'null@suse.de')
    commiter = pygit2.Signature('Git OBS Bridge', 'obsbridge@suse.de')
    message = r.comment
    if first_commit:
        ref = "HEAD"
        parents = []
        first_commit = False
    else:
        ref = repo.head.name
        parents = [repo.head.target]
        
    tree = index.write_tree()
    repo.create_commit(ref, author, commiter, message, tree, parents)

for r in revs_factory:
    #r.get_file('bash.spec')
    r.check_request()
