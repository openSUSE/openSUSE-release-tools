from osc import oscerr
from osclib.cache import Cache
from osclib.cache_manager import CacheManager
from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.stagingapi import StagingAPI
from osclib.core import attribute_value_save
import logging
import os.path
import osc.conf
import osc.core
import random
import string
import vcr

try:
    from urllib.error import HTTPError, URLError
except ImportError:
    #python 2.x
    from urllib2 import HTTPError, URLError

APIURL = 'http://localhost:3737'
PROJECT = 'openSUSE:Factory'

class StagingWorkflow(object):
    def __init__(self):
        """
        Initialize the configuration
        """
        THIS_DIR = os.path.dirname(os.path.abspath(__file__))
        oscrc = os.path.join(THIS_DIR, 'test.oscrc')

        logging.basicConfig()
        vcr_log = logging.getLogger('vcr')
        vcr_log.setLevel(logging.INFO)

        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        self.load_config()
        self.api = StagingAPI(APIURL, PROJECT)
        CacheManager.test = True
        Cache.init()
        Cache.delete_all()
        Cache.last_updated_load(APIURL)

    def load_config(self, project=PROJECT):
        self.config = Config(APIURL, project)

    def create_attribute_type(self, name):
        meta="""
        <namespace name='OSRT'>
            <modifiable_by user='Admin'/>
        </namespace>"""
        url = osc.core.makeurl(APIURL, ['attribute', 'OSRT', '_meta'])
        osc.core.http_PUT(url, data=meta)

        meta="""
        <definition name='{}' namespace='OSRT'>
            <description>A timestamp for the planned release date of an incident.</description>
            <count>1</count>
            <modifiable_by role='maintainer'/>
        </definition>""".format(name)
        url = osc.core.makeurl(APIURL, ['attribute', 'OSRT', name, '_meta'])
        osc.core.http_PUT(url, data=meta)

    def setup_remote_config(self):
        self.create_attribute_type('Config')
        attribute_value_save(APIURL, PROJECT, 'Config', 'overridden-by-local = remote-nope\n'
                                                        'remote-only = remote-indeed\n')

    def setup_rings(self):
        self.target_project = Project(name=PROJECT)
        self.ring0 = Project(name=PROJECT + ':Rings:0-Bootstrap')
        self.ring1 = Project(name=PROJECT + ':Rings:1-MinimalX')
        Package(name='wine', project=self.target_project)
        Package(name='wine', project=self.ring0)
        url = osc.core.makeurl(APIURL, ['source', self.ring0.name, 'wine', '_link'])
        osc.core.http_PUT(url, data='<link project="openSUSE:Factory"/>')

    def create_submit_request(self, project, package):
        project = Project(name=project)
        package = Package(name=package, project=project)
        package.create_commit()
        return Request(source_package=package, target_project=PROJECT)

    def create_staging(self, suffix, freeze=False):
        staging = Project(PROJECT + ':Staging:' + suffix)
        if freeze:
            FreezeCommand(self.api).perform(staging.name)
        return staging

class Project(object):
    def __init__(self, name):
        self.name = name

        meta = """
            <project name="{0}">
              <title></title>
              <description></description>
            </project>""".format(self.name)

        url = osc.core.make_meta_url('prj', self.name, APIURL)
        osc.core.http_PUT(url, data=meta)

        self.packages = []

    def add_package(self, package):
        self.packages.append(package)

    def __del__(self):
        for package in self.packages:
            del package
        url = osc.core.makeurl(APIURL, ['source', self.name])
        try:
            osc.core.http_DELETE(url)
        except HTTPError:
            pass

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
        url = osc.core.makeurl(APIURL, ['source', self.project.name, self.name])
        try:
            osc.core.http_DELETE(url)
        except HTTPError:
            # only cleanup
            pass
        print('destroyed {}/{}'.format(self.project.name, self.name))

    def create_commit(self):
        url = osc.core.makeurl(APIURL, ['source', self.project.name, self.name, 'README'])
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

    def __del__(self):
        url = osc.core.makeurl(APIURL, ['request', self.reqid], { 'newstate': 'revoked',
                                                                  'cmd': 'changestate' })
        try:
            osc.core.http_POST(url)
        except HTTPError:
            # may fail if already accepted/declined in tests
            pass
