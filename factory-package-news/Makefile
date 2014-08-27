prefix=/usr
sysconfdir=/etc
apachedir=$(sysconfdir)/apache2/conf.d
unitdir=$(prefix)/lib/systemd/system

install:
	install -d -m 755 $(DESTDIR)$(apachedir) $(DESTDIR)$(unitdir) $(DESTDIR)$(sysconfdir)/rsyslog.d
	install -m 644 systemd/* $(DESTDIR)$(unitdir)
	install -m 644 rsyslog/* $(DESTDIR)$(sysconfdir)/rsyslog.d
	install -m 644 apache/factory-package-news.conf $(DESTDIR)$(apachedir)/factory-package-news.conf.in
	install -d -m 755 $(DESTDIR)/var/lib/factory-package-news

.PHONY: install
