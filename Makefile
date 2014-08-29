prefix=/usr
datadir=$(prefix)/share
sysconfdir=/etc
unitdir=$(prefix)/lib/systemd/system
pkgdatadir=$(datadir)/osc-plugin-factory
oscplugindir=$(prefix)/lib/osc-plugins
pkgdata_SCRIPTS=$(wildcard *.py *.pl *.sh *.testcase)
pkgdata_SCRIPTS+=bs_mirrorfull findfileconflicts
pkgdata_DATA+=bs_copy osclib
SUBDIRS = factory-package-news

all:

install:
	install -d -m 755 $(DESTDIR)$(pkgdatadir) $(DESTDIR)$(unitdir) $(DESTDIR)$(oscplugindir)
	install -d -m 755 $(DESTDIR)/var/cache/repo-checker
	for i in $(pkgdata_SCRIPTS); do install -m 755 $$i $(DESTDIR)$(pkgdatadir); done
	chmod 644 $(DESTDIR)$(pkgdatadir)/{osc-*.py,*.testcase}
	for i in $(pkgdata_DATA); do cp -a $$i $(DESTDIR)$(pkgdatadir); done
	for i in osc-*.py osclib; do ln -s $(pkgdatadir)/$$i $(DESTDIR)$(oscplugindir)/$$i; done
	for i in $(SUBDIRS); do $(MAKE) -C $$i install; done

.PHONY: all install
