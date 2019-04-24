from osc import oscerr
from osclib.cache import Cache
from osclib.cache_manager import CacheManager
from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.stagingapi import StagingAPI
from osclib.core import attribute_value_save
from osclib.memoize import memoize_session_reset
import logging
import os.path
import osc.conf
import osc.core
import random
import string
import vcr
from xml.etree import cElementTree as ET

try:
    from urllib.error import HTTPError, URLError
except ImportError:
    #python 2.x
    from urllib2 import HTTPError, URLError

APIURL = 'http://localhost:3737'
PROJECT = 'openSUSE:Factory'

class StagingWorkflow(object):
    def __init__(self, project=PROJECT):
        """
        Initialize the configuration
        """
        THIS_DIR = os.path.dirname(os.path.abspath(__file__))
        oscrc = os.path.join(THIS_DIR, 'test.oscrc')

        self.apiurl = APIURL
        logging.basicConfig()
        vcr_log = logging.getLogger('vcr')
        vcr_log.setLevel(logging.INFO)

        # clear cache from other tests - otherwise the VCR is replayed depending
        # on test order, which can be harmful
        memoize_session_reset()

        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        if os.environ.get('OSC_DEBUG'):
            osc.core.conf.config['debug'] = 1
        self.project = project
        self.projects = {}
        self.requests = []
        self.groups = []
        CacheManager.test = True
        # disable caching, the TTLs break any reproduciblity
        Cache.CACHE_DIR = None
        Cache.PATTERNS = {}
        Cache.init()
        self.setup_remote_config()
        self.load_config()
        self.api = StagingAPI(APIURL, project)

    def load_config(self, project=None):
        if project is None:
            project = self.project
        self.config = Config(APIURL, project)

    def create_attribute_type(self, namespace, name, values=None):
        meta="""
        <namespace name='{}'>
            <modifiable_by user='Admin'/>
        </namespace>""".format(namespace)
        url = osc.core.makeurl(APIURL, ['attribute', namespace, '_meta'])
        osc.core.http_PUT(url, data=meta)

        meta="<definition name='{}' namespace='{}'><description/>".format(name, namespace)
        if values:
            meta += "<count>{}</count>".format(values)
        meta += "<modifiable_by role='maintainer'/></definition>"
        url = osc.core.makeurl(APIURL, ['attribute', namespace, name, '_meta'])
        osc.core.http_PUT(url, data=meta)

    def setup_remote_config(self):
        self.create_target()
        self.create_attribute_type('OSRT', 'Config', 1)
        attribute_value_save(APIURL, self.project, 'Config', 'overridden-by-local = remote-nope\n'
                                                        'remote-only = remote-indeed\n')

    def create_group(self, name):
        if name in self.groups: return
        meta = """
        <group>
          <title>{}</title>
        </group>
        """.format(name)
        self.groups.append(name)
        url = osc.core.makeurl(APIURL, ['group', name])
        osc.core.http_PUT(url, data=meta)

    def create_target(self):
        if self.projects.get('target'): return
        self.create_group('factory-staging')
        self.projects['target'] = Project(name=self.project, reviewer={'groups': ['factory-staging']})

    def setup_rings(self):
        self.create_target()
        self.projects['ring0'] = Project(name=self.project + ':Rings:0-Bootstrap')
        self.projects['ring1'] = Project(name=self.project + ':Rings:1-MinimalX')
        target_wine = Package(name='wine', project=self.projects['target'])
        self.create_link(target_wine, self.projects['ring1'])

    def create_package(self, project, package):
        project = self.create_project(project)
        return Package(name=package, project=project)

    def create_link(self, source_package, target_project, target_package=None):
        if not target_package:
            target_package = source_package.name
        target_package = Package(name=target_package, project=target_project)
        url = self.api.makeurl(['source', target_project.name, target_package.name, '_link'])
        osc.core.http_PUT(url, data='<link project="{}" package="{}"/>'.format(source_package.project.name,
                                                                               source_package.name))
        return target_package

    def create_project(self, name):
        if isinstance(name, Project):
            return name
        if name in self.projects:
            return self.projects[name]
        self.projects[name] = Project(name)
        return self.projects[name]

    def create_submit_request(self, project, package, text=None):
        project = self.create_project(project)
        package = Package(name=package, project=project)
        package.create_commit(text)
        request = Request(source_package=package, target_project=self.project)
        self.requests.append(request)
        return request

    def create_staging(self, suffix, freeze=False):
        staging = Project(self.project + ':Staging:' + suffix)
        if freeze:
            FreezeCommand(self.api).perform(staging.name)
        self.projects['staging:{}'.format(suffix)] = staging
        return staging

    def __del__(self):
        print('deleting staging workflow')
        for project in self.projects.values():
            project.remove()
        for request in self.requests:
            request.revoke()
        for group in self.groups:
            url = osc.core.makeurl(APIURL, ['group', group])
            try:
                osc.core.http_DELETE(url)
            except HTTPError:
                pass
        print('done')
        if hasattr(self.api, '_invalidate_all'):
            self.api._invalidate_all()

class Project(object):
    def __init__(self, name, reviewer={}):
        self.name = name

        meta = """
            <project name="{0}">
              <title></title>
              <description></description>
            </project>""".format(self.name)

        root = ET.fromstring(meta)
        for group in reviewer.get('groups', []):
            ET.SubElement(root, 'group', { 'groupid': group, 'role': 'reviewer'} )
        for group in reviewer.get('users', []):
            ET.SubElement(root, 'person', { 'userid': group, 'role': 'reviewer'} )

        url = osc.core.make_meta_url('prj', self.name, APIURL)
        osc.core.http_PUT(url, data=ET.tostring(root))

        self.packages = []

    def add_package(self, package):
        self.packages.append(package)

    def remove(self):
        if not self.name:
            return
        print('deleting project', self.name)
        for package in self.packages:
            package.remove()

        url = osc.core.makeurl(APIURL, ['source', self.name])
        try:
            osc.core.http_DELETE(url)
        except HTTPError:
            pass
        self.name = None

    def __del__(self):
        self.remove()

class Package(object):
    def __init__(self, name, project):
        self.name = name
        self.project = project

        meta = """
            <package project="{1}" name="{0}">
              <title></title>
              <description></description>
            </package>""".format(self.name, self.project.name)

        url = osc.core.make_meta_url('pkg', (self.project.name, self.name), APIURL)
        osc.core.http_PUT(url, data=meta)
        print('created {}/{}'.format(self.project.name, self.name))
        self.project.add_package(self)

    # delete from instance
    def __del__(self):
        self.remove()

    def create_file(self, filename, data=''):
        url = osc.core.makeurl(APIURL, ['source', self.project.name, self.name, filename])
        osc.core.http_PUT(url, data=data)

    def remove(self):
        if not self.project:
            return
        print('deleting package', self.project.name, self.name)
        url = osc.core.makeurl(APIURL, ['source', self.project.name, self.name])
        try:
            osc.core.http_DELETE(url)
        except HTTPError:
            # only cleanup
            pass

    def create_commit(self, text=None):
        url = osc.core.makeurl(APIURL, ['source', self.project.name, self.name, 'README'])
        if not text:
            text = ''.join([random.choice(string.letters) for i in range(40)])
        osc.core.http_PUT(url, data=text)

class Request(object):
    def __init__(self, source_package, target_project):
        self.source_package = source_package
        self.target_project = target_project

        self.reqid = osc.core.create_submit_request(APIURL,
                                 src_project=self.source_package.project.name,
                                 src_package=self.source_package.name,
                                 dst_project=self.target_project)
        self.revoked = False

    def __del__(self):
        self.revoke()

    def revoke(self):
        if self.revoked: return
        self.revoked = True
        url = osc.core.makeurl(APIURL, ['request', self.reqid], { 'newstate': 'revoked',
                                                                  'cmd': 'changestate' })
        try:
            osc.core.http_POST(url)
        except HTTPError:
            # may fail if already accepted/declined in tests
            pass

    def _translate_review(self, review):
        ret = {'state': review.get('state')}
        for type in ['by_project', 'by_package', 'by_user', 'by_group']:
            if not review.get(type):
                continue
            ret[type] = review.get(type)
        return ret

    def reviews(self):
        ret = []
        for review in self.xml().findall('.//review'):
            ret.append(self._translate_review(review))
        return ret

    def xml(self):
        url = osc.core.makeurl(APIURL, ['request', self.reqid])
        return ET.parse(osc.core.http_GET(url))
