SUBDIRS = factory-package-news abichecker

include Makefile.common

pkgdata_SCRIPTS=$(wildcard *.py *.pl *.sh)
pkgdata_SCRIPTS+=bs_mirrorfull findfileconflicts
pkgdata_DATA+=bs_copy osclib $(wildcard *.pm *.testcase)
package_name = openSUSE-release-tools

all:

install:
	install -d -m 755 $(DESTDIR)$(pkgdatadir) $(DESTDIR)$(unitdir) $(DESTDIR)$(oscplugindir)
	for i in $(pkgdata_SCRIPTS); do install -m 755 $$i $(DESTDIR)$(pkgdatadir); done
	chmod 644 $(DESTDIR)$(pkgdatadir)/osc-*.py
	for i in $(pkgdata_DATA); do cp -a $$i $(DESTDIR)$(pkgdatadir); done
	for i in osc-*.py osclib; do ln -s $(pkgdatadir)/$$i $(DESTDIR)$(oscplugindir)/$$i; done
	for i in $(SUBDIRS); do $(MAKE) -C $$i install; done
	install -m 644 systemd/* $(DESTDIR)$(unitdir)

check: test

test:
	# to see more add -v -d -s --nologcapture
	$(wildcard /usr/bin/nosetests-2.*)

package:
	touch dist/package/$(package_name).changes
	tar -cJf dist/package/$(package_name).tar.xz --exclude=.git* --exclude=dist/package/*.tar.xz --transform 's,^\.,$(package_name),' .

package-clean:
	rm -f dist/package/$(package_name).changes
	rm -f dist/package/$(package_name).tar.xz

.PHONY: all install test check
