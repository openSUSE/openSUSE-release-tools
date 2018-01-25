#!/usr/bin/python

import os
import shutil
import subprocess
import sys

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.conf
import osc.core
from osclib.core import devel_project_get
from osclib.core import devel_project_fallback
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

        self.skip_add_reviews = False

    def target_project_config(self, project):
        # Load project config and allow for remote entries.
        self.staging_api(project)
        config = self.staging_config[project]

        self.ignore_devel = not bool(config.get('devel-project-enforce', False))
        self.review_team = config.get('review-team')
        self.repo_checker = config.get('repo-checker')
        self.devel_whitelist = config.get('devel-whitelist', '').split()

    def check_source_submission(self, source_project, source_package, source_revision, target_project, target_package):
        super(CheckSource, self).check_source_submission(source_project, source_package, source_revision, target_project, target_package)
        self.target_project_config(target_project)

        if not self.ignore_devel:
            self.logger.info('checking if target package exists and has devel project')
            devel_project, devel_package = devel_project_get(self.apiurl, target_project, target_package)
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
                    self.review_messages['declined'] = """%s is not a devel project of %s, submit the package to a devel project first. """ \
                        """See https://en.opensuse.org/openSUSE:How_to_contribute_to_Factory#How_to_request_a_new_devel_project for details""" \
                                % (source_project, target_project)
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

        # We want to see the same package name in the devel project as in the distro; anything else calls for confusion
        if source_package != target_package:
            self.review_messages['declined'] = "No in-air renames: The package must be called the same in the devel project as in the target project"
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

        # ret = 0 : Good
        # ret = 1 : Bad
        # ret = 2 : Bad but can be non-fatal in some cases
        if ret > 1 and target_project.startswith('openSUSE:Leap:') and (source_project.startswith('SUSE:SLE-15:') or source_project.startswith('openSUSE:Factory')):
            pass
        elif ret != 0:
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
                staging_group = self.staging_group(target_project)
                if staging_group and not self.dryrun:
                    osc.core.change_review_state(self.apiurl, str(self.request.reqid), 'accepted',
                        by_group=staging_group,
                        message='skipping the staging process since only .changes modifications')
            elif self.repo_checker is not None:
                self.add_review(self.request, by_user=self.repo_checker, msg='Please review build success')

        return True

    def staging_group(self, project):
        try:
            return self.staging_api(project).cstaging_group
        except urllib2.HTTPError as e:
            if e.code != 404:
                raise e

        return None

    def is_devel_project(self, source_project, target_project):
        if source_project in self.devel_whitelist:
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
        except urllib2.HTTPError as e:
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
            project, package = devel_project_fallback(self.apiurl, action.tgt_project, action.tgt_package)
            message += ' Send this request to {}/{}.'.format(project, package)

        self.review_messages['declined'] = message
        return False

    def check_action_delete(self, request, action):
        self.target_project_config(action.tgt_project)

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

        # Decline the delete request if there is another delete/submit request against the same package
        query = "match=state/@name='new'+and+(action/target/@project='{}'+and+action/target/@package='{}')"\
                "+and+(action/@type='delete'+or+action/@type='submit')".format(action.tgt_project, action.tgt_package)
        url = osc.core.makeurl(self.apiurl, ['search', 'request'], query)
        matches = ET.parse(osc.core.http_GET(url)).getroot()
        if int(matches.attrib['matches']) > 1:
            ids = [rq.attrib['id'] for rq in matches.findall('request')]
            self.review_messages['declined'] = "There is a pending request %s to %s/%s in process." % (','.join(ids), action.tgt_project, action.tgt_package)
            return False

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

        parser.add_option('--skip-add-reviews', action='store_true', default=False, help='skip adding review after completing checks')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        bot.skip_add_reviews = self.options.skip_add_reviews

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
