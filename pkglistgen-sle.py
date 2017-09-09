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

        g.sle_minimal.solve(ignore_recommended=True)
        g.sle_module_basesystem.solve(base=g.sle_minimal)
        g.leanos.solve(ignore_recommended=True)
        g.leanos.merge_solved_group(g.sle_minimal)

#        g.release_packages_sles.solve()
#        g.release_packages_leanos.solve(base = g.sle_minimal)

        g.sle_base.solve(base=g.sle_minimal)
        g.sle_base_32bit.solve(base=g.sle_base)

        #g.ha.solve(base = g.sle_base)
        # g.ha.dump()
        #g.ha_geo.solve(base = g.ha)

        g.x11_base.solve(base=g.sle_base)
        g.x11_base_32bit.solve(base=g.x11_base)
        g.x11_extended.solve(base=g.x11_base)
        g.x11_wayland.solve(base=g.x11_base)

        g.desktop_generic_32bit.solve(base=g.x11_base_32bit)

        g.desktop_base_apps.solve(base=g.x11_extended)
        g.desktop_extended_apps.solve(base=g.desktop_base_apps)

        g.desktop_icewm.solve(base=g.x11_extended)

        g.fonts.solve(base=g.sle_minimal)

        g.fonts_initrd.solve(base=g.fonts)

        g.python.solve(base=g.sle_base)
        g.perl.solve(base=g.sle_base)
        g.php7.solve(base=g.sle_base)

        g.sle_databases.solve(base=g.sle_base)

        g.sle_webserver.solve(base=g.sle_base)
        g.legacy.solve(base=g.sle_base)
        g.nvdimm.solve(base=g.sle_base)
        g.ofed.solve(base=g.sle_base)
        g.dictionaries.solve(base=g.sle_base)
        g.virtualization.solve(base=g.sle_base, ignore_recommended=True)
        g.update_test.solve(base=g.sle_base)

        g.admin_tools.solve(base=g.sle_base)

        g.ima_applications.solve(base=g.sle_base)

        g.sle_devtools.solve(base=g.sle_base)
        g.sle_devtools_32bit.solve(base=g.sle_base)

        g.gnome_minimal.solve(base=(g.x11_extended, g.php7))
        g.gnome_minimal_32bit.solve(base=g.gnome_minimal)

        g.gnome_standard.solve(base=g.gnome_minimal)

        g.virtualization_gui.solve(base=g.gnome_standard)

        g.sle_misc_applications.solve(base=g.gnome_standard)

        g.java_base.solve(base=g.x11_base, ignore_recommended=True)
        g.java.solve(base=g.java_base)
        g.java_ibm.solve(base=g.java_base)

        g.tomcat8.solve(base=g.java_base)
        g.sle_devtools_java.solve(base=g.java_base)

        g.documentation_minimal.solve(base=g.gnome_standard)
        g.documentation_sles_basic.solve(base=g.documentation_minimal)
        g.documentation_sled_basic.solve(base=g.documentation_minimal)

        #g.sled.solve(base=g.gnome_standard, without='sles-release')
        #g.release_packages_sled.solve(base=g.sled, without='sles-release')

        g.gnome_extended.solve(base=g.gnome_standard)

        g.qt_standard.solve(base=g.x11_extended)
        g.qt_extended.solve(base=g.qt_standard)

        g.public_cloud.solve(base = g.python)

        g.sle_module_basesystem.solve(base=g.sle_minimal)
        g.sle_module_basesystem.merge_solved_group(g.sle_minimal)
        g.sle_module_basesystem.merge_solved_group(g.sle_base)
        g.sle_module_basesystem.merge_solved_group(g.sle_base_32bit)
        g.sle_module_basesystem.merge_solved_group(g.dictionaries)
        g.sle_module_basesystem.merge_solved_group(g.fonts)
        g.sle_module_basesystem.merge_solved_group(g.fonts_initrd)
        g.sle_module_basesystem.merge_solved_group(g.x11_base)
        g.sle_module_basesystem.merge_solved_group(g.x11_base_32bit)
        g.sle_module_basesystem.merge_solved_group(g.x11_wayland)
        g.sle_module_basesystem.merge_solved_group(g.virtualization)
        g.sle_module_basesystem.merge_solved_group(g.java_base)

        g.sle_module_scripting.solve(base=g.sle_module_basesystem)
        g.sle_module_scripting.merge_solved_group(g.php7)
        g.sle_module_scripting.merge_solved_group(g.perl)
        g.sle_module_scripting.merge_solved_group(g.python)
        g.sle_module_scripting.ignore(g.sle_module_basesystem)

        g.sle_module_desktop_applications.solve(base=g.x11_base)
        g.sle_module_desktop_applications.merge_solved_group(g.gnome_minimal)
        g.sle_module_desktop_applications.merge_solved_group(g.gnome_standard)
        g.sle_module_desktop_applications.merge_solved_group(g.desktop_icewm)
        g.sle_module_desktop_applications.merge_solved_group(g.desktop_base_apps)
        g.sle_module_desktop_applications.merge_solved_group(g.x11_extended)
        g.sle_module_desktop_applications.merge_solved_group(g.qt_standard)
        g.sle_module_desktop_applications.merge_solved_group(g.qt_extended)
        g.sle_module_desktop_applications.merge_solved_group(g.virtualization_gui)
        g.sle_module_desktop_applications.merge_solved_group(g.java)

        g.sle_module_desktop_applications.ignore(g.sle_module_basesystem)
        g.sle_module_desktop_applications.ignore(g.sle_module_scripting)

        g.sle_module_server_applications.solve(base=g.sle_module_desktop_applications)
        g.sle_module_server_applications.merge_solved_group(g.admin_tools)
        g.sle_module_server_applications.merge_solved_group(g.nvdimm)
        g.sle_module_server_applications.merge_solved_group(g.ofed)
        g.sle_module_server_applications.merge_solved_group(g.sle_databases)
        g.sle_module_server_applications.merge_solved_group(g.sle_misc_applications)
        g.sle_module_server_applications.merge_solved_group(g.sle_webserver)
        g.sle_module_server_applications.merge_solved_group(g.ima_applications)
        g.sle_module_server_applications.merge_solved_group(g.java_ibm)
        g.sle_module_server_applications.merge_solved_group(g.tomcat8)

        g.sle_module_server_applications.ignore(g.sle_module_basesystem)
        g.sle_module_server_applications.ignore(g.sle_module_scripting)
        g.sle_module_server_applications.ignore(g.sle_module_desktop_applications)
        g.sle_module_server_applications.ignore(g.sle_module_desktop_applications)

        g.sle_module_desktop_productivity.solve(base=g.sle_module_desktop_applications)
        g.sle_module_desktop_productivity.merge_solved_group(g.gnome_extended)
        g.sle_module_desktop_productivity.merge_solved_group(g.desktop_extended_apps)

        g.sle_module_desktop_productivity.ignore(g.sle_module_basesystem)
        g.sle_module_desktop_productivity.ignore(g.sle_module_scripting)
        g.sle_module_desktop_productivity.ignore(g.sle_module_desktop_applications)
        g.sle_module_desktop_productivity.ignore(g.sle_module_server_applications)

        g.sle_module_legacy.solve(base=g.sle_module_desktop_applications)
        g.sle_module_legacy.merge_solved_group(g.legacy)
        g.sle_module_legacy.ignore(g.sle_module_basesystem)
        g.sle_module_legacy.ignore(g.sle_module_scripting)
        g.sle_module_legacy.ignore(g.sle_module_desktop_applications)
        g.sle_module_legacy.ignore(g.sle_module_server_applications)

        g.sle_module_public_cloud.solve(base=g.sle_minimal)
        g.sle_module_public_cloud.merge_solved_group(g.public_cloud)
        g.sle_module_public_cloud.ignore(g.sle_module_basesystem)
        g.sle_module_public_cloud.ignore(g.sle_module_scripting)
        g.sle_module_public_cloud.ignore(g.sle_module_desktop_applications)
        g.sle_module_public_cloud.ignore(g.sle_module_server_applications)

        g.sle_module_hpc.solve(base=g.sle_base)
        g.sle_module_hpc.ignore(g.sle_module_basesystem)
        g.sle_module_hpc.ignore(g.sle_module_scripting)
        g.sle_module_hpc.ignore(g.sle_module_desktop_applications)
        g.sle_module_hpc.ignore(g.sle_module_server_applications)

        g.sle_module_development_tools.solve()
        g.sle_module_development_tools.merge_solved_group(g.sle_devtools)
        g.sle_module_development_tools.ignore(g.sle_module_basesystem)
        g.sle_module_development_tools.ignore(g.sle_module_scripting)
        g.sle_module_development_tools.ignore(g.sle_module_desktop_applications)
        g.sle_module_development_tools.ignore(g.sle_module_server_applications)
        g.sle_module_sap_applications.solve()

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit(app.main())

# vim: sw=4 et
