from osc.core import get_dependson
from lxml import etree as ET
from osc import cmdln
from urllib.error import HTTPError


@cmdln.option('-p', '--project', metavar='PROJECT', dest='project', default='openSUSE:Factory')
@cmdln.option('-r', '--repository', metavar='REPOSITORY', dest='repository', default='standard')
@cmdln.option('-a', '--arch', metavar='ARCH', dest='arch', default='x86_64')
def do_cycle(self, subcmd, opts, *args):
    """${cmd_name}: Try to visualize build dependencies between the package list specified

    Examples:
    osc cycle <pkg1> <pkg2> <pkg3>    # outputs a dot file showing the relation between the listed packages

    """

    if len(args) == 0:
        print("No packages were specified, no chain to draw")

    apiurl = self.get_api_url()

    print("digraph depgraph {")
    args = [pkg.strip() for pkglist in args for pkg in pkglist.split(',') if pkg.strip()]
    for pkgname in args:
        try:
            deps = ET.fromstring(get_dependson(apiurl, opts.project, opts.repository, opts.arch, [pkgname]))

            pkg = deps.find('package')
            print("\"%s\"" % pkgname)
            for deps in pkg.findall('pkgdep'):
                if deps.text in args:
                    print("\"%s\" -> \"%s\"" % (deps.text, pkgname))
        except HTTPError:
            # Ignore packages that do not exist
            print("[color=red]")
            continue

    print("}")
