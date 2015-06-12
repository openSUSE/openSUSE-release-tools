include ../Makefile.common
abicheckerdir=$(pkgdatadir)/abichecker
abichecker_SCRIPTS=$(wildcard abichecker*.py)

install:
	install -d -m 755 $(DESTDIR)$(pkgdatadir) $(DESTDIR)$(unitdir) $(DESTDIR)$(oscplugindir)
	install -d -m 755 $(DESTDIR)$(apachevhostsdir)
	install -d -m 755 $(DESTDIR)$(abicheckerdir)
	install -m 644 systemd/* $(DESTDIR)$(unitdir)
	install -m 644 apache/* $(DESTDIR)$(apachevhostsdir)
	install -d -m 755 $(DESTDIR)/var/lib/opensuse.org/abi-checker
	#
	for i in $(abichecker_SCRIPTS); do install -m 755 $$i $(DESTDIR)$(abicheckerdir); done
	for i in osclib ReviewBot.py; do ln -s ../$$i $(DESTDIR)$(abicheckerdir); done
	cp -a static templates $(DESTDIR)$(abicheckerdir)

.PHONY: install
