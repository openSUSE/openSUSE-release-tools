import os
from lxml import etree as ET
import subprocess
import unittest
import logging
import os.path
import random
import string
import sys
import traceback

from osc import conf
from osc import oscerr
import osc.conf
import osc.core
from osc.core import get_request
from osc.core import http_GET
from osc.core import makeurl

from osclib.cache import Cache
from osclib.cache_manager import CacheManager
from osclib.conf import Config
from osclib.freeze_command import FreezeCommand
from osclib.stagingapi import StagingAPI
from osclib.core import attribute_value_save
from osclib.core import request_state_change
from osclib.core import create_delete_request
from osclib.memoize import memoize_session_reset

from urllib.error import HTTPError

from abc import ABC, abstractmethod
import re

# pointing to other docker container
APIURL = 'http://api:3000'
PROJECT = 'openSUSE:Factory'

OSCRC = '/tmp/.oscrc-test'
OSCCOOKIEJAR = '/tmp/.osc_cookiejar-test'


class TestCase(unittest.TestCase):
    script = None
    script_apiurl = True
    script_debug = True
    script_debug_osc = True
    review_bots = {}

    def setUp(self):
        if os.path.exists(OSCCOOKIEJAR):
            # Avoid stale cookiejar since local OBS may be completely reset.
            os.remove(OSCCOOKIEJAR)

        self.users = []
        self.osc_user('Admin')
        self.apiurl = conf.config['apiurl']
        self.assertOBS()

    def tearDown(self):
        # Ensure admin user so that tearDown cleanup succeeds.
        self.osc_user('Admin')

    def assertOBS(self):
        url = makeurl(self.apiurl, ['about'])
        root = ET.parse(http_GET(url)).getroot()
        self.assertEqual(root.tag, 'about')

    @staticmethod
    def oscrc(userid):
        with open(OSCRC, 'w+') as f:
            f.write('\n'.join([
                '[general]',
                'apiurl = http://api:3000',
                'http_debug = false',
                'debug = false',
                'cookiejar = {}'.format(OSCCOOKIEJAR),
                '[http://api:3000]',
                'user = {}'.format(userid),
                'pass = opensuse',
                'email = {}@example.com'.format(userid),
                '',
            ]))

    def osc_user(self, userid):
        print(f'setting osc user to {userid}')
        self.users.append(userid)
        self.oscrc(userid)

        # Rather than modify userid and email, just re-parse entire config and
        # reset authentication by clearing opener to avoid edge-cases.
        self.oscParse()

    def osc_user_pop(self):
        self.users.pop()
        self.osc_user(self.users.pop())

    def oscParse(self):
        # Otherwise, will stick to first user for a given apiurl.
        conf._build_opener.last_opener = (None, None)

        # Otherwise, will not re-parse same config file.
        if 'cp' in conf.get_configParser.__dict__:
            del conf.get_configParser.cp

        conf.get_config(override_conffile=OSCRC,
                        override_no_keyring=True,
                        override_no_gnome_keyring=True)
        os.environ['OSC_CONFIG'] = OSCRC
        os.environ['OSRT_DISABLE_CACHE'] = 'true'

    def execute_script(self, args):
        """Executes the script stored in the ``script`` attribute of the current test.

        If the attributes ``script_debug`` or ``script_debug_osc`` are set to true for the current
        test, the function will add the corresponding ``--debug`` and/or ``--osc-debug`` argument
        when invoking the script.

        This function ensures the executed code is taken into account for the coverage calculation.
        """
        if self.script:
            args.insert(0, self.script)
        if self.script_debug:
            args.insert(1, '--debug')
        if self.script_debug_osc:
            args.insert(1, '--osc-debug')
        args.insert(0, '-p')
        args.insert(0, 'run')
        args.insert(0, 'coverage')

        self.execute(args)

    def execute_review_script(self, request_id, user):
        """Executes the review bot that corresponds to the script pointed by the ``script``
        attribute, targeting the given request and as the given user.

        See :func:`execute_script`.

        The script must follow the commandline syntax of a review bot.
        """
        self.osc_user(user)
        self.execute_script(['id', request_id])
        self.osc_user_pop()

    def execute_osc(self, args):
        # The wrapper allows this to work properly when osc installed via pip.
        args.insert(0, 'osc-wrapper.py')
        self.execute(args)

    def execute(self, args):
        print('$ ' + ' '.join(args))  # Print command for debugging.
        try:
            env = os.environ
            env['OSC_CONFIG'] = OSCRC
            self.output = subprocess.check_output(args, stderr=subprocess.STDOUT, text=True, env=env)
        except subprocess.CalledProcessError as e:
            print(e.output)
            raise e
        print(self.output)  # For debugging assertion failures.

    def assertOutput(self, text):
        self.assertTrue(text in self.output, '[MISSING] ' + text)

    def assertReview(self, rid, **kwargs):
        """Asserts there is a review for the given request that is assigned to the given target
        (user, group or project) and that is in the expected state.

        For example, this asserts there is a new review for the user 'jdoe' in the request 20:

        ``assertReview(20, by_user=('jdoe', 'new'))``

        :return: the found review, if the assertion succeeds
        :rtype: Review or None
        """
        request = get_request(self.apiurl, rid)
        for review in request.reviews:
            for key, value in kwargs.items():
                if hasattr(review, key) and getattr(review, key) == value[0]:
                    self.assertEqual(review.state, value[1], '{}={} not {}'.format(key, value[0], value[1]))
                    return review

        self.fail('{} not found'.format(kwargs))

    def assertReviewScript(self, request_id, user, before, after, comment=None):
        """Asserts the review script pointed by the ``script`` attribute of the current test can
        be executed and it produces the expected change in the reviews of a request.

        For this assertion to succeed the request must contain initially a review in the original
        state targeting the given user, then the script will be executed and it will be asserted
        that the request then has the final expected state (and, optionally, the expected comment).

        See :func:`execute_review_script`.

        :param request_id: request for which the script will be executed
        :type request_id: int
        :param user: target of the review, it will also be used to execute the script
        :type user: str
        :param before: expected state of the review before executing the script
        :type before: str
        :param before: expected state of the review after executing the script
        :type before: str
        :param comment: expected message for the review after executing the script
        :type comment: str
        """
        self.assertReview(request_id, by_user=(user, before))

        self.execute_review_script(request_id, user)

        review = self.assertReview(request_id, by_user=(user, after))
        if comment:
            self.assertEqual(review.comment, comment)

    def assertRequestState(self, rid, **kwargs):
        request = get_request(self.apiurl, rid)
        for key, value in kwargs.items():
            self.assertEqual(getattr(request.state, key), value)

    def randomString(self, prefix='', length=None):
        if prefix and not prefix.endswith('_'):
            prefix += '_'
        if not length:
            length = 2
        return prefix + ''.join([random.choice(string.ascii_letters) for i in range(length)])

    def setup_review_bot(self, wf, project, user, bot_class):
        """Instantiates a bot for the given project, adding the associated user as reviewer.

        :param wf: workflow containing the project, users, etc.
        :type wf: StagingWorkflow
        :param project: name of the project the bot will act on
        :type project: str
        :param user: user to create for the bot
        :type user: str
        :param bot_class: type of bot to setup
        """
        wf.create_user(user)
        prj = wf.projects[project]
        prj.add_reviewers(users=[user])

        bot_name = self.generate_bot_name(user)
        bot = bot_class(wf.apiurl, user=user, logger=logging.getLogger(bot_name))
        bot.bot_name = bot_name

        self.review_bots[user] = bot

    def execute_review_bot(self, requests, user):
        """Checks the given requests using the bot associated to the given user.

        The bot must have been previously configured via :func:`setup_review_bot`.
        """
        bot = self.review_bots[user]
        bot.set_request_ids(requests)

        self.osc_user(user)
        bot.check_requests()
        self.osc_user_pop()

    def generate_bot_name(self, user):
        """Used to ensure different test runs operate in unique namespace."""
        return '::'.join([type(self).__name__, user, str(random.getrandbits(8))])

    def assertReviewBot(self, request_id, user, before, after, comment=None):
        """Asserts the review bot associated to the given user produces the expected change in the
        reviews of a request.

        This is very similar to :func:`assertReviewScript`, but it executes the corresponding review
        bot instead of the script pointed by the ``script`` attribute.
        """
        self.assertReview(request_id, by_user=(user, before))

        self.execute_review_bot([request_id], user)

        review = self.assertReview(request_id, by_user=(user, after))
        if comment:
            self.assertEqual(review.comment, comment)


class StagingWorkflow(ABC):
    """This abstract base class is intended to setup and manipulate the environment (projects,
    users, etc.) in the local OBS instance used to tests the release tools. Thus, the derivative
    classes make easy to setup scenarios similar to the ones used during the real (open)SUSE
    development.
    """

    def __init__(self, project=PROJECT):
        """Initializes the configuration

        Note this constructor calls :func:`create_target`, which implies several projects and users
        are created right away.

        :param project: default target project
        :type project: str
        """
        THIS_DIR = os.path.dirname(os.path.abspath(__file__))
        oscrc = os.path.join(THIS_DIR, 'test.oscrc')

        # set to None so we return the destructor early in case of exceptions
        self.api = None
        self.apiurl = APIURL
        self.project = project
        self.projects = {}
        self.requests = []
        self.groups = []
        self.users = []
        self.attr_types = {}
        logging.basicConfig()

        # clear cache from other tests - otherwise the VCR is replayed depending
        # on test order, which can be harmful
        memoize_session_reset()

        osc.core.conf.get_config(override_conffile=oscrc,
                                 override_no_keyring=True,
                                 override_no_gnome_keyring=True)
        os.environ['OSC_CONFIG'] = oscrc

        if os.environ.get('OSC_DEBUG'):
            osc.core.conf.config['debug'] = 1

        CacheManager.test = True
        # disable caching, the TTLs break any reproduciblity
        Cache.CACHE_DIR = None
        Cache.PATTERNS = {}
        Cache.init()
        # Note this implicitly calls create_target()
        self.setup_remote_config()
        self.load_config()
        self.api = StagingAPI(APIURL, project)

    @abstractmethod
    def initial_config(self):
        """Values to use to initialize the 'Config' attribute at :func:`setup_remote_config`"""

    @abstractmethod
    def staging_group_name(self):
        """Name of the group in charge of the staging workflow"""

    def load_config(self, project=None):
        """Loads the corresponding :class:`osclib.Config` object into the attribute ``config``

        Such an object represents the set of values stored on the attribute 'Config' of the
        target project. See :func:`remote_config_set`.

        :param project: target project name
        :type project: str
        """

        if project is None:
            project = self.project
        self.config = Config(APIURL, project)

    def create_attribute_type(self, namespace, name, values=None):
        """Creates a new attribute type in the OBS instance."""

        if namespace not in self.attr_types:
            self.attr_types[namespace] = []

        if name not in self.attr_types[namespace]:
            self.attr_types[namespace].append(name)

        meta = """
        <namespace name='{}'>
            <modifiable_by user='Admin'/>
        </namespace>""".format(namespace)
        url = osc.core.makeurl(APIURL, ['attribute', namespace, '_meta'])
        osc.core.http_PUT(url, data=meta)

        meta = "<definition name='{}' namespace='{}'><description/>".format(name, namespace)
        if values:
            meta += "<count>{}</count>".format(values)
        meta += "<modifiable_by role='maintainer'/></definition>"
        url = osc.core.makeurl(APIURL, ['attribute', namespace, name, '_meta'])
        osc.core.http_PUT(url, data=meta)

    def setup_remote_config(self):
        """Creates the attribute 'Config' for the target project, with proper initial content.

        See :func:`remote_config_set` for more information about that attribute.

        Note this calls :func:`create_target` to ensure the target project exists.
        """
        # First ensure the existence of both the target project and the 'Config' attribute type
        self.create_target()
        self.create_attribute_type('OSRT', 'Config', 1)

        self.remote_config_set(self.initial_config(), replace_all=True)

    def remote_config_set(self, config, replace_all=False):
        """Sets the values of the 'Config' attribute for the target project.

        That attribute stores a set of values that are useful to influence the behavior of several
        tools and bots in the context of the given project. For convenience, such a collection of
        values is usually accessed using a :class:`osclib.Config` object. See :func:`load_config`.

        :param config: values to write into the attribute
        :type config: dict[str, str]
        :param replace_all: whether the previous content of 'Config' should be cleared up
        :type replace_all: bool
        """

        if not replace_all:
            config_existing = Config.get(self.apiurl, self.project)
            config_existing.update(config)
            config = config_existing

        config_lines = []
        for key, value in config.items():
            config_lines.append(f'{key} = {value}')

        attribute_value_save(APIURL, self.project, 'Config', '\n'.join(config_lines))

    def create_group(self, name, users=[]):
        """Creates a group and assigns users to it.

        If the group already exists then it just updates it users.

        :param name: name of group
        :type name: str
        :param users: list of users to be in group
        :type users: list(str)
        """
        meta = """
        <group>
          <title>{}</title>
        </group>
        """.format(name)

        if len(users):
            root = ET.fromstring(meta)
            persons = ET.SubElement(root, 'person')
            for user in users:
                ET.SubElement(persons, 'person', {'userid': user})
            meta = ET.tostring(root)

        if name not in self.groups:
            self.groups.append(name)
        url = osc.core.makeurl(APIURL, ['group', name])
        osc.core.http_PUT(url, data=meta)

    def create_user(self, name):
        """Creates a user and their home project.

        Do nothing if the user already exists.
        Password is always "opensuse".

        The home project is not really created in the OBS instance, but :func:`Project.update_meta`
        can be used to create it.

        :param name: name of the user
        :type name: str
        """
        if name in self.users:
            return
        meta = """
        <person>
          <login>{}</login>
          <email>{}@example.com</email>
          <state>confirmed</state>
        </person>
        """.format(name, name)
        self.users.append(name)
        url = osc.core.makeurl(APIURL, ['person', name])
        osc.core.http_PUT(url, data=meta)
        url = osc.core.makeurl(APIURL, ['person', name], {'cmd': 'change_password'})
        osc.core.http_POST(url, data='opensuse')
        home_project = 'home:' + name
        self.projects[home_project] = Project(home_project, create=False)

    def create_target(self):
        """Creates the main project that represents the product being developed and, as such, is
        expected to be the target for requests. It also creates all the associated projects, users
        and groups involved in the development workflow.

        In the base implementation, that includes:

            - The target project (see :func:`create_target_project`)
            - A group of staging managers including the "staging-bot" user
              (see :func:`create_staging_users`)
            - A couple of staging projects for the target one
            - The ProductVersion attribute type, that is used by the staging tools

        After the execution, the target project is indexed in the projects dictionary twice,
        by its name and as 'target'.
        """
        if self.projects.get('target'):
            return

        self.create_target_project()
        self.create_staging_users()

        self.projects['staging:A'] = Project(self.project + ':Staging:A', create=False)
        self.projects['staging:B'] = Project(self.project + ':Staging:B', create=False)

        # The ProductVersion is required for some actions, like accepting a staging project
        self.create_attribute_type('OSRT', 'ProductVersion', 1)

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

    def create_project(self, name, reviewer={}, maintainer={}, project_links=[]):
        """Creates project if it does not already exist.

        For params see the constructor of :class:`Project`

        :return: the project instance representing the given project
        :rtype: Project
        """
        if isinstance(name, Project):
            return name
        if name in self.projects:
            return self.projects[name]
        self.projects[name] = Project(name, reviewer=reviewer,
                                      maintainer=maintainer,
                                      project_links=project_links)
        return self.projects[name]

    def submit_package(self, package, project=None):
        """Creates submit request from package to target project.

        Both have to exist (Use :func:`create_submit_request` otherwise).

        :param package: package to submit
        :type package: Package
        :param project: project where to send submit request, None means use the default.
        :type project: Project or str or None
        :return: created request.
        :rtype: Request
        """
        if not project:
            project = self.project
        request = Request(source_package=package, target_project=project)
        self.requests.append(request)
        return request

    def request_package_delete(self, package, project=None):
        if not project:
            project = package.project
        request = Request(target_package=package, target_project=project, type='delete')
        self.requests.append(request)
        return request

    def create_submit_request(self, project, package, text=None, add_commit=True):
        """Creates submit request from package in specified project to default project.

        It creates project if not exist and also package.
        Package is commited with optional text.
        Note different parameters than submit_package.

        :param project: project where package will live
        :type project: Project or str
        :param package: package name to create
        :type package: str
        :param text: commit message for initial package creation
        :type text: str
        :param add_commit: whether add initial package commit. Useful to disable
               if package already exists
        :type add_commit: bool
        :return: created request.
        :rtype: Request
        """
        project = self.create_project(project)
        package = Package(name=package, project=project)
        if add_commit:
            package.create_commit(text=text)
        return self.submit_package(package)

    def __del__(self):
        if not self.api:
            return
        try:
            self.remove()
        except Exception:
            # normally exceptions in destructors are ignored but a info
            # message is displayed. Make this a little more useful by
            # printing it into the capture log
            traceback.print_exc(None, sys.stdout)

    def remove(self):
        print('deleting staging workflow')

        for project in self.projects.values():
            project.remove()
        for request in self.requests:
            request.revoke()
        for group in self.groups:
            self.remove_group(group)
        for namespace in self.attr_types:
            self.remove_attribute_types(namespace)

        print('done')

        if hasattr(self.api, '_invalidate_all'):
            self.api._invalidate_all()

    def remove_group(self, group):
        """Removes a group from the OBS instance

        :param group: name of the group to remove
        :type group: str
        """
        print('deleting group', group)
        url = osc.core.makeurl(APIURL, ['group', group])
        self._safe_delete(url)

    def remove_attribute_types(self, namespace):
        """Removes an attributes namespace and all the attribute types it contains

        :param namespace: attributes namespace to remove
        :type namespace: str
        """
        for name in self.attr_types[namespace]:
            print('deleting attribute type {}:{}'.format(namespace, name))
            url = osc.core.makeurl(APIURL, ['attribute', namespace, name, '_meta'])
            self._safe_delete(url)
        print('deleting namespace', namespace)
        url = osc.core.makeurl(APIURL, ['attribute', namespace, '_meta'])
        self._safe_delete(url)

    def _safe_delete(self, url):
        """Performs a delete request to the OBS instance, ignoring possible http errors

        :param url: url to use for the http delete request
        :type url: str
        """
        try:
            osc.core.http_DELETE(url)
        except HTTPError:
            pass

    def create_target_project(self):
        """Creates the main target project (see :func:`create_target`)"""
        p = Project(name=self.project)
        self.projects['target'] = p
        self.projects[self.project] = p

    def create_staging_users(self):
        """Creates users and groups for the staging workflow for the target project
        (see :func:`create_target`)
        """
        group = self.staging_group_name()

        self.create_user('staging-bot')
        self.create_group(group, users=['staging-bot'])
        self.projects['target'].add_reviewers(groups=[group])

        url = osc.core.makeurl(APIURL, ['staging', self.project, 'workflow'])
        data = f"<workflow managers='{group}'/>"
        osc.core.http_POST(url, data=data)


class FactoryWorkflow(StagingWorkflow):
    """A class that makes easy to setup scenarios similar to the one used during the real
    openSUSE Factory development, with staging projects, rings, etc.
    """

    def staging_group_name(self):
        return 'factory-staging'

    def initial_config(self):
        return {
            'overridden-by-local': 'remote-nope',
            'staging-group': 'factory-staging',
            'remote-only': 'remote-indeed',
        }

    def setup_rings(self, devel_project=None):
        """Creates a typical Factory setup with rings.

        It creates three projects: 'ring0', 'ring1' and the target (see :func:`create_target`).
        It also creates a 'wine' package in the target project and a link from it to ring1.
        It sets the devel project for the package if ``devel_project`` is given.

        :param devel_project: name of devel project. It must exist and contain a 'wine' package,
            otherwise OBS returns an error code.
        :type devel_project: str or None
        """
        self.create_target()
        self.projects['ring0'] = Project(name=self.project + ':Rings:0-Bootstrap')
        self.projects['ring1'] = Project(name=self.project + ':Rings:1-MinimalX')
        target_wine = Package(
            name='wine', project=self.projects['target'], devel_project=devel_project
        )
        target_wine.create_commit()
        self.create_link(target_wine, self.projects['ring1'])

    def create_staging(self, suffix, freeze=False, rings=None, with_repo=False):
        staging_key = 'staging:{}'.format(suffix)
        # do not reattach if already present
        if staging_key not in self.projects:
            staging_name = self.project + ':Staging:' + suffix
            staging = Project(staging_name, create=False, with_repo=with_repo)
            url = osc.core.makeurl(APIURL, ['staging', self.project, 'staging_projects'])
            data = '<workflow><staging_project>{}</staging_project></workflow>'
            osc.core.http_POST(url, data=data.format(staging_name))
            self.projects[staging_key] = staging
        else:
            staging = self.projects[staging_key]

        project_links = []
        if rings == 0:
            project_links.append(self.project + ":Rings:0-Bootstrap")
        if rings == 1 or rings == 0:
            project_links.append(self.project + ":Rings:1-MinimalX")

        group = self.staging_group_name()
        staging.update_meta(project_links=project_links, maintainer={'groups': [group]},
                            with_repo=with_repo)

        if freeze:
            FreezeCommand(self.api).perform(staging.name)

        return staging


class SLEWorkflow(StagingWorkflow):
    """A class that makes easy to setup scenarios similar to the one used during the real
    SLE development, with projects that inherit some packages from previous service packs, etc.
    """

    def staging_group_name(self):
        return 'sle-staging-managers'

    def initial_config(self):
        return {
            'staging-group': self.staging_group_name()
        }

    def create_target_project(self):
        """Creates the main target project (see :func:`create_target`)

        If the name of the target project follows the SLE naming convention of using "SP" to
        indicate a service pack and a prefix "GA" or "Update", this also creates all the linked
        projects needed to implement package inheritance. For example, if the target name is
        "SLE-15-SP1:Update", the method will create that project and also the projects
        "SLE-15-SP1:GA", "SLE-15:Update", "SLE-15:GA", linking each project to the corresponding one
        in the inheritance chain.
        """
        if not re.search(r'.+:(GA|Update)$', self.project):
            super().create_target_project()
            return

        suffixes = ["GA", "Update"]
        basename, number, suffix = self._prj_name_components(self.project)
        last = number * 2 + suffixes.index(suffix)

        previous = None
        for num in range(0, last + 1):
            name = self._sp_name(basename, int(num / 2))
            suffix = suffixes[num % 2]
            name = name + ":" + suffix

            if previous:
                p = Project(name, project_links=[previous])
            else:
                p = Project(name)

            self.projects[name] = p
            previous = name

        self.projects['target'] = self.projects[self.project]

    def _prj_name_components(self, prj_name):
        """Internal function to break a SLE-like name into pieces"""
        distro, suffix = prj_name.rsplit(":", 1)
        match = re.search(r'(.*)-SP(\d+)$', distro)
        if match:
            number = int(match.group(2))
            basename = match.group(1)
        else:
            number = 0
            basename = distro
        return [basename, number, suffix]

    def _sp_name(self, basename, number):
        """Internal function to build a SLE-like name"""
        if number > 0:
            return f'{basename}-SP{number}'
        else:
            return basename


class Project(object):
    """This class represents a project in the testing environment of the release tools. It usually
    corresponds to a project in the local OBS instance that is used by the tests.

    The class offers methods to setup and configure such projects to simulate the different testing
    scenarios.

    Not to be confused with the class Project in osc.core_, aimed to allow osc to manage projects
    from real OBS instances

    .. _osc.core: https://github.com/openSUSE/osc/blob/master/osc/core.py

    """

    def __init__(self, name, reviewer={}, maintainer={}, project_links=[], create=True, with_repo=False):
        """Initializes a new Project object.

        If ``create`` is False, an object is created but the project is not registered in the OBS
        instance. If ``create`` is True, the project is created in the OBS instance with the given
        meta information (by passing that information directly to :func:`update_meta`).

        TODO: a class should be introduced to represent the meta information. See :func:`get_meta`.

        :param name: project name
        :type name: str
        :param reviewer: see the corresponding parameter at :func:`update_meta`
        :param maintainer: see :func:`update_meta`
        :param project_links: see :func:`update_meta`
        :param create: whether the project should be registed in the OBS instance
        :type create: bool
        :param with_repo: see :func:`update_meta`
        """
        self.name = name
        self.packages = []

        if not create:
            return

        self.update_meta(reviewer, maintainer, project_links, with_repo=with_repo)

    def update_meta(self, reviewer={}, maintainer={}, project_links=[], with_repo=False):
        """Sets the meta information for the project in the OBS instance

        If the project does not exist in the OBS instance, calling this method will register it.

        TODO: a class should be introduced to represent the meta. See :func:`get_meta`.

        :param reviewer: see the ``'reviewer'`` key of the meta dictionary at :func:`get_meta`
        :type reviewer: dict[str, list(str)]
        :param maintainer: see the ``'maintainer'`` key of the meta dictionary at :func:`get_meta`
        :type maintainer: dict[str, list(str)]
        :param project_links: names of linked project from which it inherits
        :type project_links: list(str)
        :param with_repo: whether a repository should be created as part of the meta
        :type with_repo: bool
        """
        meta = """
            <project name="{0}">
              <title></title>
              <description></description>
            </project>""".format(self.name)

        root = ET.fromstring(meta)
        for group in reviewer.get('groups', []):
            ET.SubElement(root, 'group', {'groupid': group, 'role': 'reviewer'})
        for group in reviewer.get('users', []):
            ET.SubElement(root, 'person', {'userid': group, 'role': 'reviewer'})
        # TODO: avoid this duplication
        for group in maintainer.get('groups', []):
            ET.SubElement(root, 'group', {'groupid': group, 'role': 'maintainer'})
        for group in maintainer.get('users', []):
            ET.SubElement(root, 'person', {'userid': group, 'role': 'maintainer'})

        for link in project_links:
            ET.SubElement(root, 'link', {'project': link})

        if with_repo:
            repo = ET.SubElement(root, 'repository', {'name': 'standard'})
            ET.SubElement(repo, 'arch').text = 'x86_64'

        self.custom_meta(ET.tostring(root))

    def get_meta(self):
        """Data from the meta section of the project in the OBS instance

        TODO: a class should be introduced to represent the meta, a set of nested dictionaries
        is definitely not the way to go for the long term. The structure of the dictionary has
        to be managed at several places and the corresponding keys pollute the signature of the
        ``Project`` constructor and also other methods like :func:`update_meta`.

        Currently, the meta information is represented by a dictionary with the following keys
        and values:

        * ``'reviewer'``: contains a dictionary with two keys 'groups' and 'users', each of them
          containing a list of strings with names of the corresponding reviewers of the project
        * ``'maintainer'``: same structure as 'reviewer', but with lists of maintainer names
        * ``'project_links'``: list of names of linked projects
        * ``'with_repo'``: boolean indicating whether the meta includes some repository

        :return: the meta dictionary, see description above
        :rtype: dict[str, dict or list(str) or bool]
        """
        meta = {
            'reviewer': {'groups': [], 'users': []},
            'maintainer': {'groups': [], 'users': []},
            'project_links': [],
            'with_repo': False
        }
        url = osc.core.make_meta_url('prj', self.name, APIURL)
        data = ET.parse(osc.core.http_GET(url))
        for child in data.getroot():
            if child.tag == 'repository':
                meta['with_repo'] = True
            elif child.tag == 'link':
                meta['project_links'].append(child.attrib['project'])
            elif child.tag == 'group':
                role = child.attrib['role']
                if role not in ['reviewer', 'maintainer']:
                    continue
                meta[role]['groups'].append(child.attrib['groupid'])
            elif child.tag == 'person':
                role = child.attrib['role']
                if role not in ['reviewer', 'maintainer']:
                    continue
                meta[role]['users'].append(child.attrib['userid'])

        return meta

    def add_reviewers(self, users=[], groups=[]):
        """Adds the given reviewers to the meta information of the project

        :param users: usernames to add to the current list of reviewers
        :type users: list(str)
        :param groups: groups to add to the current list of reviewers
        :type groups: list(str)
        """
        meta = self.get_meta()
        meta['reviewer']['users'] = list(set(meta['reviewer']['users'] + users))
        meta['reviewer']['groups'] = list(set(meta['reviewer']['groups'] + groups))
        self.update_meta(**meta)

    def add_package(self, package):
        self.packages.append(package)

    def custom_meta(self, meta):
        url = osc.core.make_meta_url('prj', self.name, APIURL)
        osc.core.http_PUT(url, data=meta)

    def remove(self):
        if not self.name:
            return
        print('deleting project', self.name)
        for package in self.packages:
            package.remove()

        url = osc.core.makeurl(APIURL, ['source', self.name], {'force': 1})
        try:
            osc.core.http_DELETE(url)
        except HTTPError as e:
            if e.code != 404:
                raise e
        self.name = None

    def __del__(self):
        self.remove()


class Package(object):
    """This class represents a package in the local OBS instance used to test the release tools and
    offers methods to create and modify such packages in order to simulate the different testing
    scenarios.

    Not to be confused with the class Package in osc.core_, aimed to allow osc to manage packages
    from real OBS instances

    .. _osc.core: https://github.com/openSUSE/osc/blob/master/osc/core.py
    """

    def __init__(self, name, project, devel_project=None):
        """Creates a package in the OBS instance and instantiates an object to represent it

        :param name: Package name
        :type name: str
        :param project: project where package lives
        :type project: Project
        :param devel_project: name of devel project. Package has to already exists there,
            otherwise OBS returns 400.
        :type devel_project: str
        """

        self.name = name
        self.project = project

        meta = """
            <package project="{1}" name="{0}">
              <title></title>
              <description></description>
            </package>""".format(self.name, self.project.name)

        if devel_project:
            root = ET.fromstring(meta)
            ET.SubElement(root, 'devel', {'project': devel_project})
            meta = ET.tostring(root)

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
        except HTTPError as e:
            if e.code != 404:
                raise e
        self.project = None

    def create_commit(self, text=None, filename='README'):
        url = osc.core.makeurl(APIURL, ['source', self.project.name, self.name, filename])
        if not text:
            text = ''.join([random.choice(string.ascii_letters) for i in range(40)])
        osc.core.http_PUT(url, data=text)

    def commit_files(self, path):
        """Commits to the package the files in the given directory

        Useful to load fixtures.

        :param path: path to a directory containing the files that must be added to the package
        """
        for filename in os.listdir(path):
            # Opening as binary is needed e.g. for compressed tarball sources
            with open(os.path.join(path, filename), 'rb') as f:
                self.create_commit(filename=filename, text=f.read())


class Request(object):
    """This class represents a request in the local OBS instance used to test the release tools and
    offers methods to create and modify such requests in order to simulate the different testing
    scenarios.

    Not to be confused with the class Request in osc.core_, aimed to allow osc to create and
    manage requests on real OBS instances

    .. _osc.core: https://github.com/openSUSE/osc/blob/master/osc/core.py
    """

    def __init__(self, source_package=None, target_project=None, target_package=None, type='submit'):
        """Creates a request in the OBS instance and instantiates an object to represent it"""
        self.revoked = True

        if type == 'submit':
            self.reqid = osc.core.create_submit_request(APIURL,
                                                        src_project=source_package.project.name,
                                                        src_package=source_package.name,
                                                        dst_project=target_project,
                                                        dst_package=target_package)
            print('created submit request {}/{} -> {}'.format(
                source_package.project.name, source_package.name, target_project))
        elif type == 'delete':
            self.reqid = create_delete_request(APIURL, target_project.name, target_package.name)
        else:
            raise oscerr.WrongArgs(f'unknown request type {type}')

        self.revoked = False

    def __del__(self):
        self.revoke()

    def revoke(self):
        if self.revoked:
            return
        self.change_state('revoked')
        self.revoked = True

    def change_state(self, state):
        print(f'changing request state of {self.reqid} to {state}')

        try:
            request_state_change(APIURL, self.reqid, state)
        except HTTPError as e:
            # may fail if already accepted/declined in tests or project deleted
            if e.code != 403 and e.code != 404:
                raise e

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
