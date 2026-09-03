#!/usr/bin/python3
# (c) 2025 fvogt@suse.de
# GPLv3-only

import osc.conf
import osc.core
import logging
import ToolBase
import sys
import re
from collections import defaultdict, namedtuple
from lxml import etree as xml


class ContainerCleaner(ToolBase.ToolBase):
    def __init__(self):
        ToolBase.ToolBase.__init__(self)
        self.logger = logging.getLogger(__name__)

    def getDirEntries(self, path):
        url = self.makeurl(path)
        directory = xml.parse(self.retried_GET(url))
        return directory.xpath("entry/@name")

    def getBinaryList(self, project):
        url = self.makeurl(["build", project, "_result"], query={"view": "binarylist"})
        return xml.parse(self.retried_GET(url))

    def findSourcepkgsToDelete(self, project):
        # Get a set of all srccontainers in the project
        srccontainers = set(self.getDirEntries(["source", project]))

        # List of all binaries in the project
        resultlist = self.getBinaryList(project)

        # The bot keeps five releases of each image alive. "Same image" is defined by
        # having the same package name, flavor and architecture.
        ImageKey = namedtuple("ProvidedImage", ["pkgname", "flavor", "arch"])

        # A released image. maint_release kept as first tuple member to act for sorting.
        ReleasedImage = namedtuple("ReleasedImage", ["maint_release", "buildcontainer", "srccontainer"])

        # Sort the released packages into buckets for each image it provides:
        # {ImageKey("opensuse-tumbleweed-image", "docker", "x86_64"):
        #    [ReleasedImage(20190402134201, "container-image.20190402134201:docker", "container-image.20190402134201"), ...]}
        buckets = defaultdict(list)

        # Split a buildcontainer name like "sourcepkg.01234:flavor" into its parts
        regex_buildcontainer = re.compile(R"^([^:]+)\.([0-9]+)(:[^:]+)?$")

        for arch_result in resultlist.xpath("result"):
            arch = arch_result.get("arch")

            for binarylist in arch_result.xpath("binarylist"):
                buildcontainer = binarylist.get("package")
                if len(binarylist.xpath("binary")) == 0:
                    continue

                match = regex_buildcontainer.match(buildcontainer)
                if not match:
                    raise Exception(f"Could not parse {buildcontainer} as build container")

                pkgname, maint_release, flavor = match.group(1, 2, 3)
                srccontainer = f"{pkgname}.{maint_release}"
                if srccontainer not in srccontainers:
                    raise Exception(f"Mapped {buildcontainer} to wrong source container ({srccontainer})")

                imgbucket = ImageKey(pkgname, flavor, arch)
                logging.debug("%s provides binaries for %s through %s", buildcontainer, imgbucket, srccontainer)
                buckets[imgbucket] += [ReleasedImage(maint_release, buildcontainer, srccontainer)]

        # The list of srccontainers referenced by released images
        seen_srccontainers = set([img.srccontainer for pkg in buckets for img in buckets[pkg]])
        srccontainers_not_seen = set(srccontainers) - seen_srccontainers
        if srccontainers_not_seen:
            logging.warning(f"The following srccontainers have no binaries and will not be touched: {srccontainers_not_seen}")
        else:
            logging.debug("All srccontainers have binaries!")

        # Now go through each bucket and find out what doesn't contribute to the newest five
        srccontainers_referenced = set()
        for package in buckets:
            # Sort each bucket: Newest provider first
            buckets[package].sort(reverse=True)
            logging.debug("Found %d providers of %s", len(buckets[package]), package)

            for released_image in buckets[package][:5]:
                logging.debug(f"\t{released_image} needed")
                srccontainers_referenced.add(released_image.srccontainer)
            for released_image in buckets[package][5:]:
                logging.debug(f"\t{released_image} no longer needed")

        return seen_srccontainers - srccontainers_referenced

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
