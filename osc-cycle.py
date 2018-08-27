import osc.core
from osc.core import get_dependson
from xml.etree import cElementTree as ET

def do_cycle(self, subcmd, opts, *args):
    """${cmd_name}: Try to visualize build dependencies between the package list specified

    Examples:
    osc cycle <pkg1> <pkg2> <pkg3>    # outputs a dot file showing the relation between the listed packages

    """

    if len(args) == 0:
        print ("No packages were specified, no chain to draw")

    apiurl = self.get_api_url()

    print ("digraph depgraph {")
    for pkgname in args:
        pkgname = pkgname.strip(',')
        if len(pkgname) == 0: continue
        try:
            deps = ET.fromstring(get_dependson(apiurl, "openSUSE:Factory", "standard", "x86_64", [pkgname]))

            pkg = deps.find('package')
            print ("\"%s\"" % pkgname)
            for deps in pkg.findall('pkgdep'):
                if deps.text in args:
                    print ("\"%s\" -> \"%s\"" % (deps.text, pkgname))
        except:
            # Ignore packages that do not exist
            print ("[color=red]")
            continue

    print ("}")
