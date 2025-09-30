#!/usr/bin/python3
# (c) 2025 fvogt@suse.de
# GPLv3-only

import osc.conf
import osc.core
import logging
import ToolBase
import sys
import re
from collections import defaultdict
from lxml import etree as xml


class ContainerCleaner(ToolBase.ToolBase):
    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.logger = logging.getLogger(__name__)

    def getDirEntries(self, path):
        url = self.makeurl(path)
        directory = xml.parse(self.retried_GET(url))
        return directory.xpath("entry/@name")

    def getDirBinaries(self, path):
        url = self.makeurl(path)
        directory = xml.parse(self.retried_GET(url))
        return directory.xpath("binary/@filename")

    def findSourcepkgsToDelete(self, project):
        # Get a list of all images
        srccontainers = self.getDirEntries(["source", project])

        # Sort the released packages into buckets for each origin package:
        # {"opensuse-tumbleweed-image": ["opensuse-tumbleweed-image.20190402134201", ...]}
        buckets = defaultdict(list)
        regex_maintenance_release = re.compile(R"^(.+)\.[0-9]+$")
        for srccontainer in srccontainers:
            # Get the right bucket
            match = regex_maintenance_release.match(srccontainer)
            if match:
                # Maintenance release
                package = match.group(1)
            else:
                # Not renamed
                package = srccontainer

            buckets[package] += [srccontainer]

        for package in buckets:
            # Sort each bucket: Newest provider first
            buckets[package].sort(reverse=True)
            logging.debug("Found %d providers of %s", len(buckets[package]), package)

        # Get a hash for sourcecontainer -> arch with binaries
        # {"opensuse-tumbleweed-image.20190309164844": ["aarch64", "armv7l", "armv6l"],
        # "kubic-pause-image.20190306124139": ["x86_64", "i586"], ... }
        srccontainerarchs = defaultdict(list)

        archs = self.getDirEntries(["build", project, "containers"])
        regex_srccontainer = re.compile(R"^([^:]+)(:[^:]+)?$")
        for arch in archs:
            if arch == "local":
                continue

            buildcontainers = self.getDirEntries(["build", project, "containers", arch])
            for buildcontainer in buildcontainers:
                bins = self.getDirBinaries(["build", project, "containers", arch, buildcontainer])
                if len(bins) > 0:
                    match = regex_srccontainer.match(buildcontainer)
                    if not match:
                        raise Exception(f"Could not map {buildcontainer} to source container")

                    srccontainer = match.group(1)
                    if srccontainer not in srccontainers:
                        raise Exception(f"Mapped {buildcontainer} to wrong source container ({srccontainer})")

                    logging.debug("%s provides binaries for %s", srccontainer, arch)
                    srccontainerarchs[srccontainer] += [arch]

        # Now go through each bucket and find out what doesn't contribute to the newest five
        can_delete = []
        for package in buckets:
            # {"x86_64": 1, "aarch64": 2, ...}
            archs_found = defaultdict(lambda: 0)

            for srccontainer in buckets[package]:
                contributes = False
                for arch in srccontainerarchs[srccontainer]:
                    if archs_found[arch] < 5:
                        archs_found[arch] += 1
                        contributes = True

                if len(srccontainerarchs[srccontainer]) == 0:
                    logging.warning("%s has no binaries (any flavor/any arch) - skipping", srccontainer)
                elif contributes:
                    logging.debug("%s contributes to %s", srccontainer, package)
                else:
                    logging.info("%s does not contribute", srccontainer)
                    if len([count for count in archs_found.values() if count > 0]) == 0:
                        # If there are A, B, C and D, with only C and D providing binaries,
                        # A and B aren't deleted because they have newer sources. This is
                        # to avoid deleting something due to unforeseen circumstances, e.g.
                        # OBS didn't copy the binaries yet.
                        logging.warning("No newer provider found either, ignoring")
                    else:
                        can_delete += [srccontainer]

        return can_delete

    def run(self, project):
        packages = self.findSourcepkgsToDelete(project)

        for package in packages:
            url = self.makeurl(["source", project, package])
            if self.dryrun:
                logging.info("DELETE %s", url)
            else:
                osc.core.http_DELETE(url)


class CommandLineInterface(ToolBase.CommandLineInterface):
    def __init__(self, *args, **kwargs):
        ToolBase.CommandLineInterface.__init__(self, args, kwargs)

    def setup_tool(self):
        tool = ContainerCleaner()
        if self.options.debug:
            logging.basicConfig(level=logging.DEBUG)
        elif self.options.verbose:
            logging.basicConfig(level=logging.INFO)

        return tool

    def do_run(self, subcmd, opts, project):
        """${cmd_name}: run the Container cleaner for the specified project

        ${cmd_usage}
        ${cmd_option_list}
        """

        self.tool.run(project)


if __name__ == "__main__":
    cli = CommandLineInterface()
    sys.exit(cli.main())
