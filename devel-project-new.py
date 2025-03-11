import sys
from datetime import datetime, timezone
import cmdln
from cmdln import CmdlnOptionParser
from lxml import etree as ET

import osc.core
from osc.core import HTTPError
from osc.core import show_project_meta
from osc.core import show_package_meta
from osc.core import get_review_list
import osc.conf
from osclib.comments import CommentAPI
from osclib.core import get_request_list_with_history
from osclib.core import request_age
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

import ReviewBot

BOT_NAME='devel-project'
REMINDER='review reminder'

class DevelProject(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

    def _search(self, queries=None, **kwargs):
        # XXX should we refactor this out?
        if 'request' in kwargs:
            # get_review_list() does not support withfullhistory, but search() does.
            if queries is None:
                queries = {}
            request = queries.get('request', {})
            request['withfullhistory'] = 1
            queries['request'] = request

        return osc.core.search(self.apiurl, queries, **kwargs)

    def _devel_projects_get(self, project):
        """
        Returns a sorted list of devel projects for a given project.

        Loads all packages for a given project, checks them for a devel link and
        keeps a list of unique devel projects.
        """
        # XXX should we refactor this out?
        devel_projects = {}


        root = self._search(**{'package': f"@project='{project}'"})['package']
        for devel in root.findall('package/devel[@project]'):
            devel_projects[devel.attrib['project']] = True

        # Ensure self does not end up in list.
        if project in devel_projects:
            del devel_projects[project]

        return sorted(devel_projects)

    def _devel_projects_load(self, opts):
        api = self._staging_api(opts)
        devel_projects = api.pseudometa_file_load('devel_projects')

        if devel_projects:
            return devel_projects.splitlines()

        raise Exception('no devel projects found')


    def _staging_api(self, opts):
        Config(self.apiurl, opts.project)
        return StagingAPI(self.apiurl, opts.project)

    def _maintainers_get(self, project, package=None):
        if package:
            try:
                meta = show_package_meta(self.apiurl, project, package)
            except HTTPError as e:
                if e.code == 404:
                    # Fallback to project in the case of new package.
                    meta = show_project_meta(self.apiurl, project)
        else:
            meta = show_project_meta(self.apiurl, project)
        meta = ET.fromstringlist(meta)

        userids = []
        for person in meta.findall('person[@role="maintainer"]'):
            userids.append(person.get('userid'))

        if len(userids) == 0 and package is not None:
            # Fallback to project if package has no maintainers.
            return self._maintainers_get(project)

        return userids

    def _remind_comment(self, repeat_age, request_id, project, package=None, do_repeat=True):
        # TODO port to Gitea
        comment_api = CommentAPI(self.apiurl)
        comments = comment_api.get_comments(request_id=request_id)
        comment, _ = comment_api.comment_find(comments, BOT_NAME)

        if comment:
            if not do_repeat:
                print('  skipping due to reminder has been created')
                return
            delta = datetime.now(timezone.utc) - comment['when']
            if delta.days < repeat_age:
                print(f'  skipping due to previous reminder from {delta.days} days ago')
                return

            # Repeat notification so remove old comment.
            try:
                comment_api.delete(comment['id'])
            except HTTPError as e:
                if e.code == 403:
                    # Gracefully skip when previous reminder was by another user.
                    print('  unable to remove previous reminder')
                    return
                raise e

        userids = sorted(self._maintainers_get(project, package))
        if len(userids):
            users = ['@' + userid for userid in userids]
            message = f"{', '.join(users)}: {REMINDER}"
        else:
            message = REMINDER
        print('  ' + message)
        message = comment_api.add_marker(message, BOT_NAME)
        comment_api.add_comment(request_id=request_id, comment=message)

    def do_list(self, opts, cmd_opts):
        devel_projects = self._devel_projects_get(opts.project)
        if len(devel_projects) == 0:
            print('no devel projects found')
        else:
            out = '\n'.join(devel_projects)
            print(out)

            if cmd_opts.write:
                api = self._staging_api(opts)
                api.pseudometa_file_ensure('devel_projects', out, 'devel_projects write')

    def do_requests(self, opts, cmd_opts):
        devel_projects = self._devel_projects_load(opts)

        # Disable including source project in get_request_list() query.
        osc.conf.config['include_request_from_project'] = False
        for devel_project in devel_projects:
            requests = get_request_list_with_history(
                self.apiurl, devel_project, req_state=('new', 'review'),
                req_type='submit')
            for request in requests:
                action = request.actions[0]
                age = request_age(request).days
                if age < cmd_opts.min_age:
                    continue

                print(' '.join((
                    request.reqid,
                    '/'.join((action.tgt_project, action.tgt_package)),
                    '/'.join((action.src_project, action.src_package)),
                    f'({age} days old)',
                )))

                if cmd_opts.remind:
                    self._remind_comment(cmd_opts.repeat_age, request.reqid, action.tgt_project, action.tgt_package)

    def do_reviews(self, opts, cmd_opts):
        devel_projects = self._devel_projects_load(opts)
        config = self.platform.get_project_config(opts.project)
        # do not update reminder repeatedly if the paricular target project
        reminder_once_only_target_projects = set(config.get('reminder-once-only-target-projects', '').split())

        for devel_project in devel_projects:
            requests = get_review_list(self.apiurl, byproject=devel_project)
            for request in requests:
                # get_review_list() behavior has been changed in osc
                # https://github.com/openSUSE/osc/commit/00decd25d1a2c775e455f8865359e0d21872a0a5
                if request.state.name != 'review':
                    continue
                action = request.actions[0]
                if action.type != 'submit':
                    continue

                age = request_age(request).days
                if age < cmd_opts.min_age:
                    continue

                for review in request.reviews:
                    if review.by_project == devel_project:
                        break

                print(' '.join((
                    request.reqid,
                    '/'.join((review.by_project, review.by_package)) if review.by_package else review.by_project,
                    '/'.join((action.tgt_project, action.tgt_package)),
                    f'({age} days old)',
                )))
                if cmd_opts.remind:
                    if action.tgt_project in reminder_once_only_target_projects:
                        repeat_reminder = False
                    else:
                        repeat_reminder = True
                    self._remind_comment(cmd_opts.repeat_age, request.reqid, review.by_project, review.by_package, repeat_reminder)

def common_options(f):
    f = cmdln.option('--min-age', type=int, default=0, metavar='DAYS', help='min age of requests')(f)
    f = cmdln.option('--repeat-age', type=int, default=7, metavar='DAYS', help='age after which a new reminder will be sent')(f)
    f = cmdln.option('--remind', action='store_true', help='remind maintainers to review')(f)
    return f

class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, *args, **kwargs)
        self.clazz = DevelProject

    def get_optparser(self) -> CmdlnOptionParser:
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('-p', '--project', default='openSUSE:Factory', metavar='PROJECT',
                          help='project from which to source devel projects')
        return parser

    @cmdln.option('-w', '--write', action='store_true', help='write to pseudometa package')
    def do_list(self, subcmd, opts, *args):
        """${cmd_name}: List devel projects.

        ${cmd_usage}
        ${cmd_option_ist}
        """
        return self.checker.do_list(self.options, opts)

    @cmdln.option('-g', '--group', action='append', help='group for which to check')
    def do_maintainer(self, subcmd, opts, *args):
        """${cmd_name}: Check for relevant groups as maintainer.

        ${cmd_usage}
        ${cmd_option_list}
        """
        # TODO
        print("TODO: maintainer")
        pass

    def do_notify(self, subcmd, opts, *args):
        """${cmd_name}: Notify maintainers of their packages

        ${cmd_isage}
        ${cmd_option_list}
        """
        # TODO
        print("TODO: notify")
        pass

    @common_options
    def do_requests(self, subcmd, opts, *args):
        """${cmd_name}: List open requests.

        ${cmd_usage}
        ${cmd_option_list}"""
        return self.checker.do_requests(self.options, opts)

    @common_options
    def do_reviews(self, subcmd, opts, *args):
        """${cmd_name}: List open reviews.

        ${cmd_usage}
        ${cmd_option_list"""
        return self.checker.do_reviews(self.options, opts)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
