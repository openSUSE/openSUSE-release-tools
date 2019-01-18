#!/usr/bin/python

from osclib.core import project_source_info
from osclib.origin import origin_find
import ReviewBot
import sys


class OriginManager(ReviewBot.ReviewBot):
    def check_source_submission(self, src_project, src_package, src_rev, tgt_project, tgt_package):
        # Due to src_rev cannot use project_source_info().
        source_info = self.get_sourceinfo(src_project, src_package, src_rev)
        print(src_project, src_package, source_info.srcmd5)
        origin_new = origin_find(self.apiurl, tgt_project, tgt_package, source_info.srcmd5)

        source_info = project_source_info(self.apiurl, tgt_project, tgt_package)
        print(tgt_project, tgt_package, source_info.get('srcmd5'))
        origin = origin_find(self.apiurl, tgt_project, tgt_package, source_info.get('srcmd5'))

        print('result', origin_new, origin)


class CommandLineInterface(ReviewBot.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, *args, **kwargs)
        self.clazz = OriginManager

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
