#!/usr/bin/python

import sys

from osclib.core import depends_on
from osclib.core import maintainers_get

import ReviewBot

class RepoChecker(ReviewBot.ReviewBot):
    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.only_one_action = True
        self.request_default_return = True
        self.comment_handler = True

        # RepoChecker options.
        self.skip_cycle = False

    def check_action_delete(self, request, action):
        creator = request.get_creator()
        # Force include project maintainers in addition to package owners.
        maintainers = set(maintainers_get(self.apiurl, action.tgt_project, action.tgt_package) +
                          maintainers_get(self.apiurl, action.tgt_project)) # TODO Devel project
        if creator not in maintainers:
            self.logger.warn('{} is not one of the maintainers: {}'.format(creator, ', '.join(maintainers)))

        # TODO Include runtime dependencies instead of just build dependencies.
        what_depends_on = depends_on(self.apiurl, action.tgt_project, 'standard', [action.tgt_package], True)
        if len(what_depends_on):
            self.logger.warn('{} still required by {}'.format(action.tgt_package, ', '.join(what_depends_on)))

        if len(self.comment_handler.lines):
            self.comment_write(result='decline')
            return False

        self.logger.info('delete request is safe')
        self.comment_write(result='accept')
        return True


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = RepoChecker

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option('--skip-cycle', action='store_true', help='skip cycle check')

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.skip_cycle:
            bot.skip_cycle = self.options.skip_cycle

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
