include ../Makefile.common

prefix=/usr
sysconfdir=/etc
apachedir=$(sysconfdir)/apache2/conf.d
unitdir=$(prefix)/lib/systemd/system
announcer_subpackage=factory-package-news
pkgdata_SCRIPTS=$(wildcard *.py *.pl *.sh)

install:
	install -d -m 755 $(DESTDIR)$(pkgdatadir)/$(announcer_subpackage) $(DESTDIR)$(apachedir) $(DESTDIR)$(unitdir) $(DESTDIR)$(sysconfdir)/rsyslog.d
	for i in $(pkgdata_SCRIPTS); do install -m 755 $$i $(DESTDIR)$(pkgdatadir)/$(announcer_subpackage); done
	install -m 644 systemd/* $(DESTDIR)$(unitdir)
	install -m 644 rsyslog/* $(DESTDIR)$(sysconfdir)/rsyslog.d
	install -m 644 apache/factory-package-news.conf $(DESTDIR)$(apachedir)/factory-package-news.conf.in
	ln -s $(pkgdatadir)/$(announcer_subpackage)/announcer.py $(DESTDIR)$(bindir)/osrt-announcer

.PHONY: install
