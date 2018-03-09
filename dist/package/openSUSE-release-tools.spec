#
# spec file for package openSUSE-release-tools
#
# Copyright (c) 2017 SUSE LINUX GmbH, Nuernberg, Germany.
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

# Please submit bugfixes or comments via http://bugs.opensuse.org/
#


%global __provides_exclude ^perl.*
%define source_dir osc-plugin-factory
%define announcer_filename factory-package-news
Name:           openSUSE-release-tools
Version:        0
Release:        0
Summary:        Tools to aid in staging and release work for openSUSE/SUSE
License:        GPL-2.0+ AND MIT
Group:          Development/Tools/Other
Url:            https://github.com/openSUSE/osc-plugin-factory
Source:         %{name}-%{version}.tar.xz
BuildArch:      noarch
# Requires sr#512849 which provides osc_plugin_dir.
BuildRequires:  osc >= 0.159.0
BuildRequires:  python-PyYAML
BuildRequires:  python-cmdln
BuildRequires:  python-colorama
BuildRequires:  python-lxml
BuildRequires:  python-pycurl
BuildRequires:  python-python-dateutil
BuildRequires:  python-pyxdg
BuildRequires:  python-urlgrabber
%if 0%{?is_opensuse}
# Testing only requirements installed for `make check`.
BuildRequires:  libxml2-tools
BuildRequires:  python-httpretty
BuildRequires:  python-mock
BuildRequires:  python-nose
%endif

# Spec related requirements.
%if 0%{?is_opensuse}
BuildRequires:  apache-rpm-macros
%else
%define apache_sysconfdir %{_sysconfdir}/apache2
%endif
BuildRequires:  apache2-devel
BuildRequires:  rsyslog
BuildRequires:  systemd-rpm-macros

Requires:       python-PyYAML
Requires:       python-cmdln
Requires:       python-colorama
Requires:       python-lxml
# issue-diff.py, legal-auto.py, and openqa-maintenance.py
Requires:       python-requests
Requires:       python-pycurl
Requires:       python-python-dateutil
Requires:       python-pyxdg
Requires:       python-urlgrabber

# bs_mirrorfull
Requires:       perl-XML-Parser
Requires:       perl-Net-SSLeay

# Spec related requirements.
Requires:       osclib = %{version}

%description
Tools to aid in staging and release work for openSUSE/SUSE

The toolset consists of a variety of stand-alone scripts, review bots, osc
plugins, and automation aids.

%package devel
Summary:        Development requirements for openSUSE-release-tools
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       libxml2-tools
Requires:       python-httpretty
Requires:       python-mock
Requires:       python-nose

%description devel
Development requirements for openSUSE-release-tools to be used in conjunction
with a git clone of the development repository available from %{url}.

%package abichecker
Summary:        ABI review bot
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osclib = %{version}

%description abichecker
ABI review bot for checking OBS requests.

%package announcer
Summary:        Release announcer
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.

%description announcer
OBS product release announcer for generating email diffs summaries.

%package check-source
Summary:        Check source review bot
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.
Requires:       osclib = %{version}
Requires(pre):  shadow

%description check-source
Check source review bot that performs basic source analysis and assigns reviews.

%package leaper
Summary:        Leap-style services
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       %{name} = %{version}
Requires:       osclib = %{version}
Requires(pre):  shadow

%description leaper
Leap-style services for non-Factory projects.

%package maintenance
Summary:        Maintenance related services
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.
Requires:       osclib = %{version}
Requires(pre):  shadow

%description maintenance
Maintenance related services like incident check.

%package metrics
Summary:        Ingest relevant data to generate insightful metrics
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.
Requires:       osclib = %{version}
Requires(pre):  shadow
# TODO Requires: python-influxdb, but package does not exist in Factory, but
# present in Cloud:OpenStack:Master/python-influxdb.
Recommends:     python-influxdb
Suggests:       grafana
Suggests:       influxdb

%description metrics
Ingest relevant OBS and annotation data to generate insightful metrics.

%package repo-checker
Summary:        Repository checker service
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.
Requires:       osclib = %{version}
# repo_checker.pl
Requires:       perl-XML-Simple
Requires:       build
Requires(pre):  shadow

%description repo-checker
Repository checker service that inspects built RPMs from stagings.

%package staging-bot
Summary:        Staging bot services
Group:          Development/Tools/Other
BuildArch:      noarch
# devel-project.py
Requires:       %{name} = %{version}
Requires:       osc-plugin-staging = %{version}
# For supersede service.
Requires:       osc-plugin-check_dups = %{version}
Requires(pre):  shadow

%description staging-bot
Staging bot services and system user.

%package totest-manager
Summary:        Manages product ToTest repository
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.
Requires:       osclib = %{version}
Requires:       python-openqa_client

%description totest-manager
Manages product ToTest repository workflow and openQA interaction

%package pkglistgen
Summary:        Generates package lists in 000product
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osclib = %{version}
Requires:       obs-service-product_converter
Requires:       python-requests
Requires:       python-solv
# we use the same user as repo-checker
PreReq:         openSUSE-release-tools-repo-checker

%description pkglistgen
Generates package lists based on 000package-groups and puts them
in 000product, resp 000release-packages


%package -n osclib
Summary:        Supplemental osc libraries
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements, but for now base deps.
Requires:       %{name} = %{version}
Requires:       osc >= 0.159.0

%description -n osclib
Supplemental osc libraries utilized by release tools.

%package -n osc-plugin-check_dups
Summary:        OSC plugin to check for duplicate requests
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osc >= 0.159.0

%description -n osc-plugin-check_dups
OSC plugin to check for duplicate requests, see `osc check_dups --help`.

%package -n osc-plugin-cycle
Summary:        OSC plugin for cycle visualization
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osc >= 0.159.0

%description -n osc-plugin-cycle
OSC plugin for cycle visualization, see `osc cycle --help`.

%package -n osc-plugin-staging
Summary:        OSC plugin for the staging workflow
Group:          Development/Tools/Other
BuildArch:      noarch
# devel-project.py needs 0.160.0 for get_request_list(withfullhistory) param.
Requires:       osc >= 0.160.0
Requires:       osclib = %{version}

%description -n osc-plugin-staging
OSC plugin for the staging workflow, see `osc staging --help`.

%package -n osc-plugin-vdelreq
Summary:        OSC plugin to check for virtually accepted request
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osc >= 0.159.0

%description -n osc-plugin-vdelreq
OSC plugin to check for virtually accepted request, see `osc vdelreq --help`.

%prep
%setup -q

%build
make %{?_smp_mflags}

%check
%if 0%{?is_opensuse}
# TODO openSUSE/osc-plugin-factory#1221: decide how to handle integration tests
# make check
%endif

%install
%make_install \
  grafana_dashboards_dir="%{_localstatedir}/lib/grafana/dashboards/%{name}" \
  oscplugindir="%{osc_plugin_dir}" \
  VERSION="%{version}"

%pre announcer
getent passwd osrt-announcer > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-leaper" osrt-announcer
exit 0

%postun announcer
%systemd_postun

%pre check-source
getent passwd osrt-check-source > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-check-source" osrt-check-source
exit 0

%postun check-source
%systemd_postun

%pre leaper
getent passwd osrt-leaper > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-leaper" osrt-leaper
exit 0

%postun leaper
%systemd_postun

%pre maintenance
getent passwd osrt-maintenance > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-maintenance" osrt-maintenance
exit 0

%postun maintenance
%systemd_postun

%pre metrics
getent passwd osrt-metrics > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-metrics" osrt-metrics
exit 0

%postun metrics
%systemd_postun
# If grafana-server.service is enabled then restart it to load new dashboards.
if [ -x /usr/bin/systemctl ] && /usr/bin/systemctl is-enabled grafana-server ; then
  /usr/bin/systemctl try-restart --no-block grafana-server
fi

%pre repo-checker
getent passwd osrt-repo-checker > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-repo-checker" osrt-repo-checker
exit 0

%postun repo-checker
%systemd_postun

%pre staging-bot
getent passwd osrt-staging-bot > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-staging-bot" osrt-staging-bot
exit 0

%postun staging-bot
%systemd_postun

%pre totest-manager
getent passwd osrt-totest-manager > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-totest-manager" osrt-totest-manager
exit 0

%postun totest-manager
%systemd_postun
if [ -x /usr/bin/systemctl ] ; then
  instances=($(systemctl list-units -t service --full | grep -oP osrt-totest-manager@[^.]+ || true))
  if [ ${#instances[@]} -gt 0 ] ; then
    systemctl try-restart --no-block ${instances[@]}
  fi
fi

%postun pkglistgen
%systemd_postun

%files
%defattr(-,root,root,-)
%doc README.asciidoc
%{_datadir}/%{source_dir}
%exclude %{_datadir}/%{source_dir}/abichecker
%exclude %{_datadir}/%{source_dir}/%{announcer_filename}
%exclude %{_datadir}/%{source_dir}/check_maintenance_incidents.py
%exclude %{_datadir}/%{source_dir}/check_source.pl
%exclude %{_datadir}/%{source_dir}/check_source.py
%exclude %{_datadir}/%{source_dir}/devel-project.py
%exclude %{_datadir}/%{source_dir}/leaper.py
%exclude %{_datadir}/%{source_dir}/manager_42.py
%exclude %{_datadir}/%{source_dir}/metrics
%exclude %{_datadir}/%{source_dir}/metrics.py
%exclude %{_datadir}/%{source_dir}/metrics_release.py
%exclude %{_datadir}/%{source_dir}/pkglistgen.py
%exclude %{_datadir}/%{source_dir}/repo_checker.pl
%exclude %{_datadir}/%{source_dir}/repo_checker.py
%exclude %{_datadir}/%{source_dir}/suppkg_rebuild.py
%exclude %{_datadir}/%{source_dir}/totest-manager.py
%exclude %{_datadir}/%{source_dir}/osclib
%exclude %{_datadir}/%{source_dir}/osc-check_dups.py
%exclude %{_datadir}/%{source_dir}/osc-cycle.py
%exclude %{_datadir}/%{source_dir}/osc-staging.py
%exclude %{_datadir}/%{source_dir}/osc-vdelreq.py
%exclude %{_datadir}/%{source_dir}/update_crawler.py
%dir %{_sysconfdir}/openSUSE-release-tools

%files devel
%defattr(-,root,root,-)
# Non-empty for older products.
%doc README.asciidoc

%files abichecker
%defattr(-,root,root,-)
%{apache_sysconfdir}/vhosts.d/opensuse-abi-checker.conf.in
%{_datadir}/%{source_dir}/abichecker
%{_tmpfilesdir}/opensuse-abi-checker.conf
%{_unitdir}/opensuse-abi-checker.service

%files announcer
%defattr(-,root,root,-)
%doc %{announcer_filename}/README.asciidoc
%{_bindir}/osrt-announcer
%{apache_sysconfdir}/conf.d/%{announcer_filename}.conf.in
%{_datadir}/%{source_dir}/%{announcer_filename}
%config(noreplace) %{_sysconfdir}/openSUSE-release-tools/announcer
%config(noreplace) %{_sysconfdir}/rsyslog.d/%{announcer_filename}.conf
%{_unitdir}/osrt-announcer@.service
%{_unitdir}/osrt-announcer@.timer

%files check-source
%defattr(-,root,root,-)
%{_bindir}/osrt-check_source
%{_datadir}/%{source_dir}/check_source.pl
%{_datadir}/%{source_dir}/check_source.py
%{_unitdir}/osrt-check-source.service
%{_unitdir}/osrt-check-source.timer

%files leaper
%defattr(-,root,root,-)
%{_bindir}/osrt-leaper
%{_bindir}/osrt-leaper-crawler-*
%{_bindir}/osrt-manager_42
%{_bindir}/osrt-update_crawler
%{_datadir}/%{source_dir}/leaper.py
%{_datadir}/%{source_dir}/manager_42.py
%{_datadir}/%{source_dir}/update_crawler.py
%{_unitdir}/osrt-leaper-crawler@.service
%{_unitdir}/osrt-leaper-crawler@.timer
%{_unitdir}/osrt-leaper-manager@.service
%{_unitdir}/osrt-leaper-manager@.timer
%{_unitdir}/osrt-leaper-review.service
%{_unitdir}/osrt-leaper-review.timer
%config(noreplace) %{_sysconfdir}/openSUSE-release-tools/manager_42

%files maintenance
%defattr(-,root,root,-)
%{_bindir}/osrt-check_maintenance_incidents
%{_datadir}/%{source_dir}/check_maintenance_incidents.py
%{_unitdir}/osrt-maintenance-incidents.service
%{_unitdir}/osrt-maintenance-incidents.timer

%files metrics
%defattr(-,root,root,-)
%{_bindir}/osrt-metrics
%{_datadir}/%{source_dir}/metrics
%{_datadir}/%{source_dir}/metrics.py
%{_datadir}/%{source_dir}/metrics_release.py
# To avoid adding grafana as BuildRequires since it does not live in same repo.
%dir %attr(0750, grafana, grafana) %{_localstatedir}/lib/grafana
%dir %{_localstatedir}/lib/grafana/dashboards
%{_localstatedir}/lib/grafana/dashboards/%{name}
%{_unitdir}/osrt-metrics@.service
%{_unitdir}/osrt-metrics@.timer
%{_unitdir}/osrt-metrics-release@.service
%{_unitdir}/osrt-metrics-release@.timer

%files repo-checker
%defattr(-,root,root,-)
%{_bindir}/osrt-repo_checker
%{_datadir}/%{source_dir}/repo_checker.pl
%{_datadir}/%{source_dir}/repo_checker.py
%{_unitdir}/osrt-repo-checker.service
%{_unitdir}/osrt-repo-checker.timer
%{_unitdir}/osrt-repo-checker-project_only@.service
%{_unitdir}/osrt-repo-checker-project_only@.timer

%files staging-bot
%defattr(-,root,root,-)
%{_bindir}/osrt-devel-project
%{_bindir}/osrt-suppkg_rebuild
%{_datadir}/%{source_dir}/devel-project.py
%{_datadir}/%{source_dir}/suppkg_rebuild.py
%{_unitdir}/osrt-staging-bot-daily@.service
%{_unitdir}/osrt-staging-bot-daily@.timer
%{_unitdir}/osrt-staging-bot-devel-list.service
%{_unitdir}/osrt-staging-bot-devel-list.timer
%{_unitdir}/osrt-staging-bot-regular@.service
%{_unitdir}/osrt-staging-bot-regular@.timer
%{_unitdir}/osrt-staging-bot-reminder.service
%{_unitdir}/osrt-staging-bot-reminder.timer
%{_unitdir}/osrt-staging-bot-supersede@.service
%{_unitdir}/osrt-staging-bot-supersede@.timer
%{_unitdir}/osrt-staging-bot-support-rebuild@.service
%{_unitdir}/osrt-staging-bot-support-rebuild@.timer

%files totest-manager
%defattr(-,root,root,-)
%{_bindir}/osrt-totest-manager
%{_datadir}/%{source_dir}/totest-manager.py
%{_unitdir}/osrt-totest-manager@.service

%files pkglistgen
%defattr(-,root,root,-)
%{_bindir}/osrt-pkglistgen
%{_datadir}/%{source_dir}/pkglistgen.py
%{_unitdir}/osrt-pkglistgen@.service
%{_unitdir}/osrt-pkglistgen@.timer

%files -n osclib
%defattr(-,root,root,-)
%{_datadir}/%{source_dir}/osclib
%{osc_plugin_dir}/osclib

%files -n osc-plugin-check_dups
%defattr(-,root,root,-)
%{_datadir}/%{source_dir}/osc-check_dups.py
%{osc_plugin_dir}/osc-check_dups.py

%files -n osc-plugin-cycle
%defattr(-,root,root,-)
%{_datadir}/%{source_dir}/osc-cycle.py
%{osc_plugin_dir}/osc-cycle.py

%files -n osc-plugin-staging
%defattr(-,root,root,-)
%{_datadir}/%{source_dir}/osc-staging.py
%{osc_plugin_dir}/osc-staging.py

%files -n osc-plugin-vdelreq
%defattr(-,root,root,-)
%{_datadir}/%{source_dir}/osc-vdelreq.py
%{osc_plugin_dir}/osc-vdelreq.py

%changelog
