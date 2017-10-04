#!/usr/bin/python

import os
import shutil
import subprocess
import sys

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.core
import urllib2
import ReviewBot
from check_maintenance_incidents import MaintenanceChecker

class CheckSource(ReviewBot.ReviewBot):

    SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.only_one_action = True

        self.maintbot = MaintenanceChecker(*args, **kwargs)

        self.ignore_devel = False
        self.review_team = 'opensuse-review-team'
        self.repo_checker = 'repo-checker'
        self.staging_group = 'factory-staging'
        self.skip_add_reviews = False

    def check_source_submission(self, source_project, source_package, source_revision, target_project, target_package):
        super(CheckSource, self).check_source_submission(source_project, source_package, source_revision, target_project, target_package)

        if not self.ignore_devel:
            # Check if target package exists and has devel project.
            devel_project, devel_package = self.get_devel_project(target_project, target_package)
            if devel_project:
                if (source_project != devel_project or source_package != devel_package) and \
                   not(source_project == target_project and source_package == target_package):
                    # Not from proper devel project/package and not self-submission.
                    self.review_messages['declined'] = 'Expected submission from devel package %s/%s' % (devel_project, devel_package)
                    return False
            else:
                # Check to see if other packages exist with the same source project
                # which indicates that the project has already been used as devel.
                if not self.is_devel_project(source_project, target_project):
                    self.review_messages['declined'] = '%s is not a devel project of %s, submit the package to a devel project first' % (source_project, target_project)
                    return False

        # Checkout and see if renaming package screws up version parsing.
        dir = os.path.expanduser('~/co/%s' % self.request.reqid)
        if os.path.exists(dir):
            self.logger.warn('directory %s already exists' % dir)
            shutil.rmtree(dir)
        os.makedirs(dir)
        os.chdir(dir)

        old_info = {'version': None}
        try:
            CheckSource.checkout_package(self.apiurl, target_project, target_package, pathname=dir,
                         server_service_files=True, expand_link=True)
            shutil.rmtree(os.path.join(target_package, '.osc'))
            os.rename(target_package, '_old')
            old_info = self.package_source_parse(target_project, target_package)
        except urllib2.HTTPError:
            self.logger.error('failed to checkout %s/%s' % (target_project, target_package))

        CheckSource.checkout_package(self.apiurl, source_project, source_package, revision=source_revision,
                        pathname=dir, server_service_files=True, expand_link=True)
        os.rename(source_package, target_package)
        shutil.rmtree(os.path.join(target_package, '.osc'))

        new_info = self.package_source_parse(source_project, source_package, source_revision)
        if new_info['name'] != target_package:
            shutil.rmtree(dir)
            self.review_messages['declined'] = "A package submitted as %s has to build as 'Name: %s' - found Name '%s'" % (target_package, target_package, new_info['name'])
            return False

        # Run check_source.pl script and interpret output.
        source_checker = os.path.join(CheckSource.SCRIPT_PATH, 'check_source.pl')
        civs = ''
        new_version = None
        if old_info['version'] and old_info['version'] != new_info['version']:
            new_version = new_info['version']
            civs += "NEW_VERSION='{}' ".format(new_version)
        civs += 'LC_ALL=C perl %s _old %s 2>&1' % (source_checker, target_package)
        p = subprocess.Popen(civs, shell=True, stdout=subprocess.PIPE, close_fds=True)
        ret = os.waitpid(p.pid, 0)[1]
        checked = p.stdout.readlines()

        output = '  '.join(checked).translate(None, '\033')
        os.chdir('/tmp')

        if ret != 0:
            shutil.rmtree(dir)
            self.review_messages['declined'] = "Output of check script:\n" + output
            return False

        shutil.rmtree(dir)
        self.review_messages['accepted'] = 'Check script succeeded'

        if len(checked):
            self.review_messages['accepted'] += "\n\nOutput of check script (non-fatal):\n" + output

        if not self.skip_add_reviews:
            if self.review_team is not None:
                self.add_review(self.request, by_group=self.review_team, msg='Please review sources')

            if self.only_changes():
                self.logger.debug('only .changes modifications')
                if not self.dryrun:
                    osc.core.change_review_state(self.apiurl, str(self.request.reqid), 'accepted',
                        by_group=self.staging_group,
                        message='skipping the staging process since only .changes modifications')
            elif self.repo_checker is not None:
                self.add_review(self.request, by_user=self.repo_checker, msg='Please review build success')

        return True

    def is_devel_project(self, source_project, target_project):
        # Load project config and allow for remote entries.
        self.staging_api(target_project)
        devel_whitelist = self.staging_config[target_project].get('devel-whitelist', '').split()
        if source_project in devel_whitelist:
            return True

        # Allow any projects already used as devel projects for other packages.
        search = {
            'package': "@project='%s' and devel/@project='%s'" % (target_project, source_project),
        }
        result = osc.core.search(self.apiurl, **search)
        return result['package'].attrib['matches'] != '0'

    @staticmethod
    def checkout_package(*args, **kwargs):
        _stdout = sys.stdout
        sys.stdout = open(os.devnull, 'wb')
        try:
            result = osc.core.checkout_package(*args, **kwargs)
        finally:
            sys.stdout = _stdout
        return result

    def package_source_parse(self, project, package, revision=None):
        query = {'view': 'info', 'parse': 1}
        if revision:
            query['rev'] = revision
        url = osc.core.makeurl(self.apiurl, ['source', project, package], query)

        ret = {'name': None, 'version': None}

        try:
            xml = ET.parse(osc.core.http_GET(url)).getroot()
        except urllib2.HTTPError, e:
            self.logger.error('ERROR in URL %s [%s]' % (url, e))
            return ret

        # ET boolean check fails.
        if xml.find('name') is not None:
            ret['name'] = xml.find('name').text

        if xml.find('version') is not None:
            ret['version'] = xml.find('version').text

        return ret

    def only_changes(self):
        u = osc.core.makeurl(self.apiurl, ['request', self.request.reqid],
                             {'cmd': 'diff', 'view': 'xml'})
        try:
            diff = ET.parse(osc.core.http_POST(u)).getroot()
            for f in diff.findall('action/sourcediff/files/file/*[@name]'):
                if not f.get('name').endswith('.changes'):
                    return False
            return True
        except:
            pass
        return False

    def check_action_add_role(self, request, action):
        # Decline add_role request (assumed the bot acting on requests to Factory or similar).
        message = 'Roles to packages are granted in the devel project, not in %s.' % action.tgt_project

        if action.tgt_package is not None:
            message += ' Please send this request to %s/%s.' % self.get_devel_project(action.tgt_project, action.tgt_package)

        self.review_messages['declined'] = message
        return False

    def check_action_delete(self, request, action):
        if action.tgt_repository is not None:
            if action.tgt_project.startswith('openSUSE:'):
                self.review_messages['declined'] = 'The repositories in the openSUSE:* namespace ' \
                    'are managed by the Release Managers. For suggesting changes, send a mail ' \
                    'to opensuse-releaseteam@opensuse.org with an explanation of why the change ' \
                    'makes sense.'
                return False
            else:
                self.review_messages['accepted'] = 'unhandled: removing repository'
                return True
        try:
            result = osc.core.show_project_sourceinfo(self.apiurl, action.tgt_project, True, (action.tgt_package))
            root = ET.fromstring(result)
        except urllib2.HTTPError:
            return None

        # Decline the delete request against linked package.
        links = root.findall('sourceinfo/linked')
        if links is None or len(links) == 0:
            # Utilize maintbot to add devel project review if necessary.
            self.maintbot.check_one_request(request)

            if not self.skip_add_reviews and self.repo_checker is not None:
                self.add_review(self.request, by_user=self.repo_checker, msg='Is this delete request safe?')
            return True
        else:
            linked = links[0]
            linked_project = linked.get('project')
            linked_package = linked.get('package')
            self.review_messages['declined'] = "This is an incorrect request, it's a linked package to %s/%s" % (linked_project, linked_package)
            return False

    def check_action__default(self, request, action):
        self.review_messages['accepted'] = 'Unhandled request type %s.' % (action.type)
        return True

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = CheckSource

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('--ignore-devel', action='store_true', default=False, help='ignore devel projects for target package')
        parser.add_option('--review-team', metavar='GROUP', help='review team group added to requests')
        parser.add_option('--repo-checker', metavar='USER', help='repo checker user added after accepted review')
        parser.add_option('--staging-group', metavar='GROUP', help='group used by staging process')
        parser.add_option('--skip-add-reviews', action='store_true', default=False, help='skip adding review after completing checks')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.ignore_devel:
            bot.ignore_devel = self.options.ignore_devel
        if self.options.review_team:
            if self.options.review_team == 'None':
                self.options.review_team = None
            bot.review_team = self.options.review_team
        if self.options.repo_checker:
            if self.options.repo_checker == 'None':
                self.options.repo_checker = None
            bot.repo_checker = self.options.repo_checker
        if self.options.staging_group:
            bot.staging_group = self.options.staging_group
        bot.skip_add_reviews = self.options.skip_add_reviews

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
