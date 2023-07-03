#!/usr/bin/python3
from lxml import etree as ET
import sys
import ToolBase
from osclib.core import fileinfo_ext_all
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osclib.cleanup_rings import CleanupRings

class Cleanup32bit(ToolBase.ToolBase):
    def run(self, prj: str, arch: str):
        Config(self.apiurl, prj)
        cr = CleanupRings(StagingAPI(self.apiurl, prj))
        cr.whitelist = set(["wine", "wine-nine-standalone", "wine:staging"])
        # -32bit flavors only needed if pam-32bit is installed
        cr.whitelist.add("gnome-keyring-pam")
        cr.whitelist.add("pam_kwallet")

        cr.fill_pkginfo(prj, "standard", arch)

        # Make sure those pkgs are also installable
        for wppra in [("openSUSE:Factory:NonFree", "steam", "standard", "x86_64")]:
            (wprj, wpkg, wrepo, warch) = wppra
            for fileinfo in fileinfo_ext_all(self.apiurl, wprj, wrepo, warch, wpkg):
                for providedby in fileinfo.findall('requires_ext/providedby[@name]'):
                    name = providedby.get('name')
                    # Those are not built as i586
                    if "libgcc" in name or "libstdc++" in name:
                        continue

                    if name.endswith("-32bit"):
                        name = name[:-len("-32bit")]
                        cr.whitelist.add(cr.bin2src[name])

        all_needed_sources = cr.check_depinfo(prj, "i586", True)

        print("Not needed:")
        print("\n".join([src for src in sorted(cr.sources) if src not in all_needed_sources]))

class CommandLineInterface(ToolBase.CommandLineInterface):
    def get_optparser(self):
        parser = ToolBase.CommandLineInterface.get_optparser(self)
        parser.add_option("-p", "--project", dest="project",
                          help="project to process (default: openSUSE:Factory)",
                          default="openSUSE:Factory")
        parser.add_option("-a", "--arch", dest="arch",
                          help="arch to process (default: i586)",
                          default="i586")
        return parser

    def setup_tool(self):
        return Cleanup32bit()

    def do_run(self, subcmd, opts, *packages):
        """${cmd_name}: Go through all packages in the given project that build
        for the given arch and check whether they are necessary for the project
        to fulfill build and runtime deps for certain packages.

        ${cmd_usage}
        ${cmd_option_list}
        """
        self.tool.run(self.options.project, self.options.arch)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
