from colorama import Fore


class RebuildCommand(object):
    KEY_COLOR = {
        0: Fore.YELLOW,
        1: Fore.CYAN,
    }
    CODE_COLOR = {
        'ok': Fore.GREEN,
        'skipped': Fore.WHITE,
    }

    def __init__(self, api):
        self.api = api

    def perform(self, stagings=None, force=False):
        if not stagings:
            stagings = self.api.get_staging_projects()

        for staging in stagings:
            status = self.api.project_status(staging)
            rebuilt = self.api.rebuild_broken(status, not force)
            for key, code in rebuilt:
                key = [self.KEY_COLOR.get(i, '') + part + Fore.RESET for i, part in enumerate(key)]
                print('rebuild {} {}'.format(
                    '/'.join(key),
                    self.CODE_COLOR.get(code, Fore.RED) + code + Fore.RESET))
