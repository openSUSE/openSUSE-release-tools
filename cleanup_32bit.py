#!/usr/bin/python3
from lxml import etree as ET
import sys
import ToolBase
from osclib.core import fileinfo_ext_all
from osclib.conf import Config
from osclib.stagingapi import StagingAPI
from osclib.cleanup_rings import CleanupRings

class Cleanup32bit(ToolBase.ToolBase):
    def run(self, prj: str, arch: str, verbose: bool=False):
        Config(self.apiurl, prj)
        cr = CleanupRings(StagingAPI(self.apiurl, prj))
        cr.force_required = {
            "wine": "wine", "wine-nine-standalone": "wine",
            "wine:staging": "wine",
            "gstreamer": "boo#1210244",
            "gstreamer-plugins-base": "boo#1210244",
            "gstreamer-plugins-bad": "boo#1210244",
            "gstreamer-plugins-good": "boo#1210244",
            "gstreamer-plugins-ugly": "boo#1210244",
            "gstreamer-plugins-libav": "boo#1210244",
            "mangohud": "boo#1210199",
            "gamemode": "boo#1210199",
            "alsa-plugins": "boo#1210304",
            "alsa-oss": "boo#1210137",
            "apitrace": "boo#1210305",
            "Mesa-demo": "boo#1210145",
            "vulkan-tools": "boo#1210145",
            "xf86-video-intel": "boo#1210145",
            "grub2": "Creates grub2-i386-efi for x86_64",
            "python:python-base": "File deps: some texlive stuff needs python2 and snobol4",
            "snobol4": "File deps: some texlive stuff needs python2 and snobol4",
            "gnome-keyring": "32bit PAM stack",
            "pam_kwallet": "32bit PAM stack",
            "libnvidia-egl-wayland": "boo#1214917",
        }

        cr.fill_pkginfo(prj, "standard", arch)

        # _builddepinfo only has builddeps which might trigger a rebuild,
        # but Preinstall, Support and service packages don't. Look at the
        # actual builddep of a randomly chosen (tm) package to get the former,
        # check_depinfo handles obs-service-*.
        for bdep in cr.package_get_bdeps(prj, "glibc", "standard", arch):
            if bdep not in cr.force_required:
                cr.force_required[bdep] = "bdep of glibc"

        # Make sure those pkgs are also installable
        for wppra in [("openSUSE:Factory:NonFree", "steam", "standard", "x86_64")]:
            (wprj, wpkg, wrepo, warch) = wppra
            for fileinfo in fileinfo_ext_all(self.apiurl, wprj, wrepo, warch, wpkg):
                for providedby in fileinfo.findall('requires_ext/providedby[@name]'):
                    name = providedby.get('name')
                    # Those are not built as i586
                    if name.startswith("libgcc") or name.startswith("libstdc++"):
                        continue

                    if name.endswith("-32bit"):
                        name = name[:-len("-32bit")]
                        cr.force_required[cr.bin2src[name]] = "Runtime dep of" + wpkg

        pkgdeps = cr.check_depinfo(prj, "i586", True)

        print("Not needed:")
        print("\n".join([src for src in sorted(cr.sources) if src not in pkgdeps]))

        print("List of onlybuilds:")
        print("%ifarch %ix86")
        if verbose:
            print("\n".join([f"# {pkgdeps[src]}\nBuildFlags: onlybuild:{src}" for src in sorted(pkgdeps)]))
        else:
            print("\n".join([f"BuildFlags: onlybuild:{src}" for src in sorted(pkgdeps)]))

        print("%endif")

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
        self.tool.run(self.options.project, self.options.arch, verbose=self.options.verbose)

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())
