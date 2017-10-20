# Copyright (C) 2017 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

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
