import sys
import cmdln
from cmdln import CmdlnOptionParser

import osc.core
from osclib.stagingapi import StagingAPI

import ReviewBot

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

    def _staging_api(self, opts):
        return StagingAPI(self.apiurl, opts.project)

    def list(self, opts, cmd_options):
        devel_projects = self._devel_projects_get(opts.project)
        if len(devel_projects) == 0:
            print('no devel projects found')
        else:
            out = '\n'.join(devel_projects)
            print(out)

            if cmd_options.write:
                api = self._staging_api(opts)
                api.pseudometa_file_ensure('devel_projects', out, 'devel_projects write')


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
        return self.checker.list(self.options, opts)

    @cmdln.option('-g', '--group', action='append', help='group for which to check')
    def do_maintainer(self, subcmd, opts, *args):
        """${cmd_name}: Check for relevant groups as maintainer.

        ${cmd_usage}
        ${cmd_option_list}
        """
        # TODO
        print("TODO: maintainer")
        pass

    def do_requests(self, subcmd, opts, *args):
        """${cmd_name}: List open requests.

        ${cmd_usage}
        ${cmd_option_list}"""
        # TODO
        print("TODO: requests")
        pass

    def do_reviews(self, subcmd, opts, *args):
        """${cmd_name}: List open reviews.

        ${cmd_usage}
        ${cmd_option_list"""
        #TODO
        print("TODO: reviews")
        pass

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
