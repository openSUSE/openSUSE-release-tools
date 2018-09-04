SUBDIRS = factory-package-news abichecker

include Makefile.common

pkgdata_BINS = $(shell find * -maxdepth 0 -executable -type f)
pkgdata_SCRIPTS=$(wildcard *.py *.pl *.sh)
pkgdata_SCRIPTS+=bs_mirrorfull findfileconflicts
pkgdata_DATA+=bs_copy metrics osclib $(wildcard *.pm *.testcase)
VERSION = "build-$(shell date +%F)"

all:

install:
	install -d -m 755 $(DESTDIR)$(bindir) $(DESTDIR)$(pkgdatadir) $(DESTDIR)$(unitdir) $(DESTDIR)$(oscplugindir) $(DESTDIR)$(sysconfdir)/$(package_name) $(DESTDIR)$(grafana_provisioning_dir)/dashboards $(DESTDIR)$(grafana_provisioning_dir)/datasources
	for i in $(pkgdata_SCRIPTS); do install -m 755 $$i $(DESTDIR)$(pkgdatadir); done
	chmod 644 $(DESTDIR)$(pkgdatadir)/osc-*.py
	for i in $(pkgdata_DATA); do cp -a $$i $(DESTDIR)$(pkgdatadir); done
	for i in osc-*.py osclib; do ln -s $(pkgdatadir)/$$i $(DESTDIR)$(oscplugindir)/$$i; done
	for i in $(SUBDIRS); do $(MAKE) -C $$i install; done
	install -m 644 systemd/* $(DESTDIR)$(unitdir)
	sed -i "s/VERSION = '.*'/VERSION = '$(VERSION)'/" \
	  $(DESTDIR)$(pkgdatadir)/osclib/common.py
	for i in $(pkgdata_BINS); do ln -s $(pkgdatadir)/$$i $(DESTDIR)$(bindir)/osrt-$${i%.*}; done
	install -m 755 script/* $(DESTDIR)$(bindir)
	ln -s $(pkgdatadir)/metrics/access/aggregate.php $(DESTDIR)$(bindir)/osrt-metrics-access-aggregate
	ln -s $(pkgdatadir)/metrics/access/ingest.php $(DESTDIR)$(bindir)/osrt-metrics-access-ingest
	cp -R config/* $(DESTDIR)$(sysconfdir)/$(package_name)
	for dir in dashboards datasources ; do ln -s $(pkgdatadir)/metrics/grafana/provisioning/$$dir.yaml \
	  $(DESTDIR)$(grafana_provisioning_dir)/$$dir/$(package_name).yaml ; done
	sed -i "s|OSRT_DATA_DIR|$(pkgdatadir)|" \
	  $(DESTDIR)$(pkgdatadir)/metrics/grafana/provisioning/dashboards.yaml \
	  $(DESTDIR)$(unitdir)/osrt-metrics-telegraf.service

check: test

test:
	# to see more add -v -d -s --nologcapture
	$(wildcard /usr/bin/nosetests-2.*) -c .noserc

package:
	touch dist/package/$(package_name).changes
	tar -cJf dist/package/$(package_name)-0.tar.xz --exclude=.git* --exclude=dist/package/*.tar.xz --transform 's,^\.,$(package_name)-0,' .

package-clean:
	rm -f dist/package/$(package_name).changes
	rm -f dist/package/$(package_name).tar.xz

.PHONY: all install test check
