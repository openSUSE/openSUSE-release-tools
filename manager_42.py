#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2015 SUSE Linux GmbH
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

import argparse
import itertools
import logging
import sys
from xml.etree import cElementTree as ET

import osc.conf
import osc.core
import urllib2
import sys

from osclib.memoize import memoize

OPENSUSE = 'openSUSE:42'
SLE = 'SUSE:SLE-12:Update'

makeurl = osc.core.makeurl
http_GET = osc.core.http_GET
http_DELETE = osc.core.http_DELETE
http_PUT = osc.core.http_PUT


class UpdateCrawler(object):
    def __init__(self, from_prj):
        self.from_prj = from_prj
        self.apiurl = osc.conf.config['apiurl']
        self.debug = osc.conf.config['debug']
        self.project_mapping = {}
        for prj in ['SUSE:SLE-12:Update', 'SUSE:SLE-12:GA']:
            self.project_mapping[prj] = 'openSUSE:42:SLE12-Picks'
        self.project_mapping['openSUSE:Factory'] = 'openSUSE:42:Factory-Copies'

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        root = ET.parse(
            http_GET(makeurl(self.apiurl,
                             ['source', project],
                             query=query))).getroot()
        packages = [i.get('name') for i in root.findall('entry')]
        return packages

    @memoize()
    def _get_source_package(self, project, package, revision):
        opts = { 'view': 'info' }
        if revision:
            opts['rev'] = revision
        return http_GET(makeurl(self.apiurl,
                                ['source', project, package], opts)).read()
    
    def get_latest_request(self, project, package):
        history = http_GET(makeurl(self.apiurl,
                                   ['source', project, package, '_history'])).read()
        root = ET.fromstring(history)
        requestid = None
        # latest commit's request - if latest commit is not a request, ignore the package
        for r in root.findall('revision'):
            requestid = r.find('requestid')
        if requestid is None:
            return None
        return requestid.text

    def get_request_infos(self, requestid):
        request = http_GET(makeurl(self.apiurl,
                                   ['request', requestid])).read()
        root = ET.fromstring(request)
        action = root.find('.//action')
        source = action.find('source')
        target = action.find('target')
        project = source.get('project')
        package = source.get('package')
        rev = source.get('rev')
        return ( project, package, rev, target.get('package') )

    def remove_packages(self, project, packages):
        for package in packages:
            url = makeurl(self.apiurl, ['source', project, package])
            try:
                http_DELETE(url)
            except urllib2.HTTPError, err:
                if err.code == 404:
                    # not existant package is ok, we delete them all
                    pass
                else:
                    # If the package was there bug could not be delete, raise the error
                    raise

    # copied from stagingapi - but the dependencies are too heavy
    def create_package_container(self, project, package):
        """
        Creates a package container without any fields in project/package
        :param project: project to create it
        :param package: package name
        """
        dst_meta = '<package name="{}"><title/><description/></package>'
        dst_meta = dst_meta.format(package)

        url = makeurl(self.apiurl, ['source', project, package, '_meta'])
        print "PUT", url
        http_PUT(url, data=dst_meta)

    def _link_content(self, sourceprj, sourcepkg, rev):
        root = ET.fromstring(self._get_source_package(sourceprj, sourcepkg, rev))
        srcmd5 = root.get('srcmd5')
        vrev = root.get('vrev')
        link = "<link project='{}' package='{}' rev='{}' vrev='{}'/>"
        return link.format(sourceprj, sourcepkg, srcmd5, vrev)

    def upload_link(self, project, package, link_string):
        url = makeurl(self.apiurl, ['source', project, package, '_link'])
        print "PUT", url
        http_PUT(url, data=link_string)

    def link_packages(self, packages, sourceprj, sourcepkg, sourcerev, targetprj, targetpkg):
        print packages, sourceprj, sourcepkg, sourcerev, targetpkg
        self.remove_packages('openSUSE:42:SLE12-Picks', packages)
        self.remove_packages('openSUSE:42:Factory-Copies', packages)
        self.remove_packages('openSUSE:42:SLE-Pkgs-With-Overwrites', packages)

        self.create_package_container(targetprj, targetpkg)
        link = self._link_content(sourceprj, sourcepkg, sourcerev)
        self.upload_link(targetprj, targetpkg, link)

        for package in [ p for p in packages if p != targetpkg ]:
            link = "<link cicount='copy' package='{}' />".format(targetpkg)
            self.create_package_container(targetprj, package)
            self.upload_link(targetprj, package, link)

        self.remove_packages('openSUSE:42', packages)

    def crawl(self):
        """Main method of the class that run the crawler."""

        packages = self.get_source_packages(self.from_prj, expand=False)
        packages = [ p for p in packages if not p.startswith('_') ]
        requests = dict()

        left_packages = []
        
        for package in packages:
            requestid = self.get_latest_request(self.from_prj, package)
            if requestid is None:
                print package, "is not from request"
                left_packages.append(package)
                continue
            if requestid in requests:
                requests[requestid].append(package)
            else:
                requests[requestid] = [package]

        for request, packages in requests.items():
            sourceprj, sourcepkg, sourcerev, targetpkg = self.get_request_infos(request)
            if not sourceprj in self.project_mapping:
                print "source", sourceprj
                left_packages = left_packages + packages
                continue
            print request, packages, sourceprj, sourcepkg, sourcerev, targetpkg
            targetprj = self.project_mapping[sourceprj]
            self.link_packages(packages, sourceprj, sourcepkg, sourcerev, targetprj, targetpkg)

        return left_packages

    def try_to_find_left_packages(self, packages):
        for package in packages:
            root = ET.fromstring(self._get_source_package(self.from_prj, package, None))
            print ET.tostring(root)
            
def main(args):
    # Configure OSC
    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    uc = UpdateCrawler(args.from_prj)
    #lp = uc.crawl()
    lp = ['ant-antlr', 'ant-junit', 'antlr-bootstrap', 'bluez', 'cross-aarch64-binutils', 'cross-aarch64-gcc48-icecream-backend', 'cross-arm-binutils', 'cross-armv6hl-gcc48-icecream-backend', 'cross-armv7hl-gcc48-icecream-backend', 'cross-avr-binutils', 'cross-hppa-binutils', 'cross-hppa-gcc48-icecream-backend', 'cross-hppa64-binutils', 'cross-i386-binutils', 'cross-i386-gcc48-icecream-backend', 'cross-ia64-binutils', 'cross-ia64-gcc48-icecream-backend', 'cross-m68k-binutils', 'cross-mips-binutils', 'cross-ppc-binutils', 'cross-ppc-gcc48-icecream-backend', 'cross-ppc64-binutils', 'cross-ppc64-gcc48-icecream-backend', 'cross-ppc64le-binutils', 'cross-ppc64le-gcc48-icecream-backend', 'cross-s390-binutils', 'cross-s390-gcc48-icecream-backend', 'cross-s390x-binutils', 'cross-s390x-gcc48-icecream-backend', 'cross-sparc-binutils', 'cross-sparc64-binutils', 'cross-spu-binutils', 'cross-x86_64-binutils', 'cross-x86_64-gcc48-icecream-backend', 'gcc48-testresults', 'glibc-testsuite', 'glibc-utils', 'installation-images-openSUSE', 'iproute2-doc', 'java-1_7_0-openjdk-bootstrap', 'jython', 'kde-branding-openSUSE', 'kernel-debug', 'kernel-default', 'kernel-desktop', 'kernel-docs', 'kernel-ec2', 'kernel-lpae', 'kernel-obs-build', 'kernel-obs-qa', 'kernel-obs-qa-xen', 'kernel-pae', 'kernel-pv', 'kernel-source', 'kernel-syms', 'kernel-vanilla', 'kernel-xen', 'krb5-mini', 'libdbus-c++', 'libffi48', 'libgcj48', 'libsndfile-progs', 'lldb', 'log4j-mini', 'mozilla-nss', 'openssh-askpass-gnome', 'orc', 'patterns-openSUSE', 'pm-utils', 'polkit-default-privs', 'postgresql93-libs', 'python-base', 'python-doc', 'python-libxml2', 'python-magic', 'python-nose-doc', 'python3-gobject', 'python3-kde4', 'python3-py-doc', 'python3-rpm', 'rpm-python', 'rpmlint', 'rpmlint-mini', 'rpmlint-tests', 'systemd-mini', 'unzip-rcc', 'xml-commons-apis-bootstrap', 'xmlbeans', 'xmlbeans-mini', 'knewstuff', 'kmediaplayer', 'knotifyconfig', 'knotifications', 'kjobwidgets', 'kitemviews', 'kjsembed', 'kjs', 'libxcb', 'kparts', 'kpackage', 'kwayland', 'kwrited5', 'kio-extras5', 'kmenuedit5', 'libkdecoration2', 'libkscreen2', 'skelcd-control-openSUSE', 'xf86driproto', 'kglobalaccel', 'kguiaddons', 'khtml', 'ki18n', 'kiconthemes', 'kidletime', 'kimageformats', 'kinit', 'kio', 'kitemmodels', 'kapidox', 'milou5', 'libksysguard5', 'plasma5-mediacenter', 'kactivities5', 'plasma5-sdk', 'xf86-input-void', 'go-go-md2man', 'xf86-video-fbdev', 'xf86-video-intel', 'xf86-video-cirrus', 'xf86-video-dummy', 'xf86-video-nv', 'go', 'xf86-video-mga', 'xf86-video-nouveau', 'MozillaFirefox', 'xf86-video-sis', 'libxshmfence', 'xf86-input-wacom', 'docker', 'branding-openSUSE', 'kemoticons', 'kdoctools', 'kdnssd-framework', 'kdewebkit', 'kdesu', 'kdesignerplugin', 'kdelibs4support', 'kded', 'kdeclarative', 'kdbusaddons', 'xorg-x11-server', 'xf86-input-synaptics', 'gnome-shell', 'gdm', 'xf86-video-vmware', 'xf86-video-vesa', 'xf86dgaproto', 'xf86bigfontproto', 'xf86miscproto', 'frameworkintegration', 'xf86vidmodeproto', 'xf86rushproto', 'xineramaproto', 'evieproto', 'xf86-video-qxl', 'kde-gtk-config5', 'kfilemetadata5', 'kscreen5', 'ksshaskpass5', 'ksysguard5', 'xf86-video-r128', 'kcoreaddons', 'kcrash', 'kbookmarks', 'kcmutils', 'karchive', 'kauth', 'kconfig', 'kconfigwidgets', 'kcodecs', 'kcompletion', 'libXi', 'glu', 'libXfont', 'polkit-kde-agent-5', 'xf86-video-ati', 'baloo5', 'libqt5-qtbase', 'xf86-video-ast', 'dmxproto', 'dri2proto', 'compositeproto', 'damageproto', 'Mesa', 'bigreqsproto', 'skelcd-openSUSE', 'bluez-qt', 'attica-qt5', 'dri3proto', 'extra-cmake-modules', 'trapproto', 'scrnsaverproto', 'ant', 'slf4j', 'libQtWebKit4', 'plasma5-workspace-wallpapers', 'kbproto', 'inputproto', 'libepoxy', 'libdrm', 'fontcacheproto', 'fixesproto', 'glproto', 'fontsproto', 'ninja', 'llvm', 'xf86-input-evdev', 'python', 'docker-distribution', 'xtrans', 'xproxymngproto', 'xproto', 'usbutils', 'plasma5-openSUSE', 'plasma5-session', 'plasma5-addons', 'docker-compose', 'presentproto', 'printproto', 'pthread-stubs', 'python-Mako', 'python-MarkupSafe', 'python-nose', 'randrproto', 'recordproto', 'renderproto', 'resourceproto', 'plasma5-desktop', 'go-blackfriday', 'go-net', 'khotkeys5', 'kinfocenter5', 'kde-cli-tools5', 'powerdevil5', 'plasma5-workspace', 'khelpcenter5', 'go-text', 'bluedevil5', 'breeze', 'kcm_sddm', 'xmlgraphics-fop', 'ktexteditor', 'ktextwidgets', 'krunner', 'kservice', 'kpty', 'kross', 'kpeople5', 'kplotting', 'xextproto', 'xcmiscproto', 'xcb-proto', 'windowswmproto', 'videoproto', 'util-macros', 'kunitconversion', 'kwallet', 'kwin5', 'oxygen5', 'plasma-nm5', 'systemsettings5', 'threadweaver', 'sonnet', 'kxmlrpcclient5', 'kxmlgui', 'kwindowsystem', 'kwidgetsaddons', 'solid', 'plasma-framework', 'libKF5NetworkManagerQt', 'libKF5ModemManagerQt']
    uc.try_to_find_left_packages(lp)

if __name__ == '__main__':
    description = 'Create SR from SLE to the new openSUSE:42 project for '\
                  'every new update.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', metavar='URL', help='API URL')
    parser.add_argument('-d', '--debug', action='store_true',
                        help='print info useful for debuging')
    parser.add_argument('-f', '--from', dest='from_prj', metavar='PROJECT',
                        help='project where to get the updates (default: %s)' % OPENSUSE,
                        default=OPENSUSE)

    args = parser.parse_args()

    # Set logging configuration
    logging.basicConfig(level=logging.DEBUG if args.debug
                        else logging.INFO)

    sys.exit(main(args))
