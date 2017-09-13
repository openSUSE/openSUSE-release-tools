#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2017 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import pkglistgen
import sys


class CommandLineInterface(pkglistgen.CommandLineInterface):

    def _solve(self):
        class G(object):
            True

        g = G()

        for group in self.tool.groups.values():
            setattr(g, group.safe_name, group)

        g.leanos.inherit(g.sle_minimal)
        g.leanos.solve()
        
        g.sle_module_basesystem.inherit(g.sle_minimal)
        g.sle_module_basesystem.inherit(g.sle_base)
        g.sle_module_basesystem.inherit(g.sle_base_32bit)
        g.sle_module_basesystem.inherit(g.dictionaries)
        g.sle_module_basesystem.inherit(g.fonts)
        g.sle_module_basesystem.inherit(g.fonts_initrd)
        g.sle_module_basesystem.inherit(g.x11_base)
        g.sle_module_basesystem.inherit(g.x11_base_32bit)
        g.sle_module_basesystem.inherit(g.x11_wayland)
        g.sle_module_basesystem.inherit(g.virtualization)
        g.sle_module_basesystem.inherit(g.java_base)
        g.sle_module_basesystem.solve()

        g.sle_module_scripting.inherit(g.sle_minimal)
        g.sle_module_scripting.inherit(g.php7)
        g.sle_module_scripting.inherit(g.perl)
        g.sle_module_scripting.inherit(g.python)
        g.sle_module_scripting.solve()
        g.sle_module_scripting.ignore(g.sle_module_basesystem)

        g.sle_module_desktop_applications.inherit(g.sle_minimal)
        g.sle_module_desktop_applications.inherit(g.gnome_minimal)
        g.sle_module_desktop_applications.inherit(g.gnome_standard)
        g.sle_module_desktop_applications.inherit(g.desktop_icewm)
        g.sle_module_desktop_applications.inherit(g.desktop_base_apps)
        g.sle_module_desktop_applications.inherit(g.x11_extended)
        g.sle_module_desktop_applications.inherit(g.qt_standard)
        g.sle_module_desktop_applications.inherit(g.qt_extended)
        g.sle_module_desktop_applications.inherit(g.virtualization_gui)
        g.sle_module_desktop_applications.inherit(g.java)
        g.sle_module_desktop_applications.solve()
        g.sle_module_desktop_applications.ignore(g.sle_module_basesystem)
        g.sle_module_desktop_applications.ignore(g.sle_module_scripting)

        g.sle_module_server_applications.inherit(g.sle_minimal)
        g.sle_module_server_applications.inherit(g.admin_tools)
        g.sle_module_server_applications.inherit(g.nvdimm)
        g.sle_module_server_applications.inherit(g.ofed)
        g.sle_module_server_applications.inherit(g.sle_databases)
        g.sle_module_server_applications.inherit(g.sle_misc_applications)
        g.sle_module_server_applications.inherit(g.sle_webserver)
        g.sle_module_server_applications.inherit(g.ima_applications)
        g.sle_module_server_applications.inherit(g.java_ibm)
        g.sle_module_server_applications.inherit(g.tomcat8)
        g.sle_module_server_applications.solve()
        g.sle_module_server_applications.ignore(g.sle_module_basesystem)
        g.sle_module_server_applications.ignore(g.sle_module_scripting)
        g.sle_module_server_applications.ignore(g.sle_module_desktop_applications)
        g.sle_module_server_applications.ignore(g.sle_module_desktop_applications)

        g.sle_module_desktop_productivity.inherit(g.sle_minimal)
        g.sle_module_desktop_productivity.inherit(g.gnome_extended)
        g.sle_module_desktop_productivity.inherit(g.desktop_extended_apps)
        g.sle_module_desktop_productivity.solve()
        g.sle_module_desktop_productivity.ignore(g.sle_module_basesystem)
        g.sle_module_desktop_productivity.ignore(g.sle_module_scripting)
        g.sle_module_desktop_productivity.ignore(g.sle_module_desktop_applications)
        g.sle_module_desktop_productivity.ignore(g.sle_module_server_applications)

        g.sle_module_legacy.inherit(g.sle_minimal)
        g.sle_module_legacy.inherit(g.legacy)
        g.sle_module_legacy.solve()
        g.sle_module_legacy.ignore(g.sle_module_basesystem)
        g.sle_module_legacy.ignore(g.sle_module_scripting)
        g.sle_module_legacy.ignore(g.sle_module_desktop_applications)
        g.sle_module_legacy.ignore(g.sle_module_server_applications)

        g.sle_module_public_cloud.inherit(g.sle_minimal)
        g.sle_module_public_cloud.inherit(g.public_cloud)
        g.sle_module_public_cloud.solve()
        g.sle_module_public_cloud.ignore(g.sle_module_basesystem)
        g.sle_module_public_cloud.ignore(g.sle_module_scripting)
        g.sle_module_public_cloud.ignore(g.sle_module_desktop_applications)
        g.sle_module_public_cloud.ignore(g.sle_module_server_applications)

        g.sle_module_hpc.inherit(g.sle_minimal)
        g.sle_module_hpc.solve()
        g.sle_module_hpc.ignore(g.sle_module_basesystem)
        g.sle_module_hpc.ignore(g.sle_module_scripting)
        g.sle_module_hpc.ignore(g.sle_module_desktop_applications)
        g.sle_module_hpc.ignore(g.sle_module_server_applications)

        g.sle_module_development_tools.inherit(g.sle_minimal)
        g.sle_module_development_tools.inherit(g.sle_devtools)
        g.sle_module_development_tools.inherit(g.sle_devtools_32bit)
        g.sle_module_development_tools.solve()
        g.sle_module_development_tools.ignore(g.sle_module_basesystem)
        g.sle_module_development_tools.ignore(g.sle_module_scripting)
        g.sle_module_development_tools.ignore(g.sle_module_desktop_applications)
        g.sle_module_development_tools.ignore(g.sle_module_server_applications)

        g.sle_module_sap_applications.inherit(g.sle_minimal)
        g.sle_module_sap_applications.solve()
        g.sle_module_sap_applications.ignore(g.sle_module_basesystem)
        g.sle_module_sap_applications.ignore(g.sle_module_scripting)
        g.sle_module_sap_applications.ignore(g.sle_module_desktop_applications)
        g.sle_module_sap_applications.ignore(g.sle_module_server_applications)


if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
