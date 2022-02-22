#
# spec file for package openSUSE-release-tools
#
# Copyright (c) 2022 SUSE LLC
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

# Please submit bugfixes or comments via https://bugs.opensuse.org/
#


%global __provides_exclude ^perl.*
%define source_dir openSUSE-release-tools
%define announcer_filename factory-package-news
Name:           openSUSE-release-tools
Version:        0
Release:        0
Summary:        Tools to aid in staging and release work for openSUSE/SUSE
License:        GPL-2.0-or-later AND MIT
Group:          Development/Tools/Other
URL:            https://github.com/openSUSE/openSUSE-release-tools
Source:         %{name}-%{version}.tar.xz
BuildArch:      noarch
# Requires sr#704176
BuildRequires:  osc >= 0.165.1
BuildRequires:  python3-PyYAML
BuildRequires:  python3-cmdln
BuildRequires:  python3-colorama
BuildRequires:  python3-lxml
BuildRequires:  python3-osc
BuildRequires:  python3-pycurl
BuildRequires:  python3-python-dateutil
BuildRequires:  python3-pyxdg

# Spec related requirements.
%if 0%{?is_opensuse}
BuildRequires:  apache-rpm-macros
%else
%define apache_sysconfdir %{_sysconfdir}/apache2
%endif
BuildRequires:  apache2-devel
BuildRequires:  rsyslog
BuildRequires:  systemd-rpm-macros

Requires:       python3-PyYAML
Requires:       python3-cmdln
Requires:       python3-colorama
Requires:       python3-lxml
# issue-diff.py, legal-auto.py, and openqa-maintenance.py
Requires:       python3-pycurl
Requires:       python3-python-dateutil
Requires:       python3-pyxdg
Requires:       python3-requests

# bs_mirrorfull
Requires:       perl-Net-SSLeay
Requires:       perl-XML-Parser

# Spec related requirements.
Requires:       osclib = %{version}

# no longer supported
Obsoletes:      osc-plugin-check_dups < 20210528
# vdelreq is no longer needed/supported; delete requests are handled immediately again
Obsoletes:      osc-plugin-vdelreq < 20210528

# Avoid needlessly building on s390x and such in various repos.
# Must include noarch for older systems even though it makes no sense due to
# https://bugzilla.redhat.com/show_bug.cgi?id=1298668.
ExclusiveArch:  noarch x86_64

%description
Tools to aid in staging and release work for openSUSE/SUSE

The toolset consists of a variety of stand-alone scripts, review bots, osc
plugins, and automation aids.

%package abichecker
Summary:        ABI review bot
Group:          Development/Tools/Other
Requires:       osclib = %{version}
BuildArch:      noarch

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
# check_source.pl
Requires:       obs-service-download_files
Requires:       obs-service-format_spec_file
Requires:       obs-service-source_validator
Requires:       osclib = %{version}
Requires:       perl-Text-Diff
Requires(pre):  shadow
BuildArch:      noarch

%description check-source
Check source review bot that performs basic source analysis and assigns reviews.

%package maintenance
Summary:        Maintenance related services
Group:          Development/Tools/Other
# TODO Update requirements.
Requires:       osclib = %{version}
Requires(pre):  shadow
BuildArch:      noarch

%description maintenance
Maintenance related services like incident check.

%package metrics
Summary:        Ingest relevant data to generate insightful metrics
Group:          Development/Tools/Other
# TODO Update requirements.
Requires:       osclib = %{version}
Requires(pre):  shadow
Suggests:       grafana
BuildArch:      noarch
%if 0%{?suse_version} > 1500
Requires:       influxdb
Requires:       python3-influxdb
Requires:       telegraf
%else
Suggests:       influxdb
Suggests:       python3-influxdb
Suggests:       telegraf
%endif

%description metrics
Ingest relevant OBS and annotation data to generate insightful metrics.

%package metrics-access
Summary:        Ingest access logs to generate metrics
Group:          Development/Tools/Other
Requires:       %{name}-metrics = %{version}
# Used to stream log files.
Requires:       curl
Requires:       php > 7
# Used to install influxdb/influxdb-php.
Requires:       php-composer
# pgrep used in aggregate.php
Requires:       procps
# xzcat for decompressing log files.
Requires:       xz
BuildArch:      noarch

%description metrics-access
Ingest download.o.o Apache access logs and generate metrics.

%package origin-manager
Summary:        Package origin management tools
Group:          Development/Tools/Other
Requires:       osc-plugin-origin = %{version}
Requires:       osclib = %{version}
Requires(pre):  shadow
BuildArch:      noarch

%description origin-manager
Tools for managing the origin of package sources and keeping them in sync.

%package repo-checker
Summary:        Repository checker service
Group:          Development/Tools/Other
# write_repo_susetags_file.pl
Requires:       build
# TODO Update requirements.
Requires:       osclib = %{version}
Requires:       perl-XML-Simple
Requires(pre):  shadow
BuildArch:      noarch

%package obs-operator
Summary:        Server used to perform staging operations
Group:          Development/Tools/Other
Requires:       osc-plugin-origin = %{version}
Requires:       osc-plugin-staging = %{version}
Requires(pre):  shadow
BuildArch:      noarch

%description obs-operator
Server used to perform staging operations as a service instead of requiring
the osc staging plugin to be utilized directly.

%description repo-checker
Repository checker service that inspects built RPMs from stagings.

%package staging-bot
Summary:        Staging bot services
Group:          Development/Tools/Other
# devel-project.py
Requires:       %{name} = %{version}
Requires:       osc-plugin-staging = %{version}
Requires(pre):  shadow
BuildArch:      noarch

%description staging-bot
Staging bot services and system user.

%package pkglistgen
Summary:        Generates package lists in 000product
Group:          Development/Tools/Other
# for compressing the .packages files in 000update-repos
Requires:       %{_bindir}/xz
Requires:       obs-service-product_converter
Requires:       osclib = %{version}
Requires:       python3-requests
Requires:       python3-solv
# we use the same user as repo-checker
PreReq:         openSUSE-release-tools-repo-checker
BuildArch:      noarch

%description pkglistgen
Generates package lists based on 000package-groups and puts them
in 000product, resp 000release-packages

%package -n osclib
Summary:        Supplemental osc libraries
Group:          Development/Tools/Other
# TODO Update requirements, but for now base deps.
Requires:       %{name} = %{version}
Requires:       osc >= 0.165.1
Requires:       python3-osc
BuildArch:      noarch

%description -n osclib
Supplemental osc libraries utilized by release tools.

%package -n osc-plugin-cycle
Summary:        OSC plugin for cycle visualization
Group:          Development/Tools/Other
Requires:       osc >= 0.165.1
Requires:       osclib = %{version}
BuildArch:      noarch

%description -n osc-plugin-cycle
OSC plugin for cycle visualization, see `osc cycle --help`.

%package -n osc-plugin-origin
Summary:        OSC plugin for origin management
Group:          Development/Tools/Other
Requires:       osc >= 0.165.1
Requires:       osclib = %{version}
BuildArch:      noarch

%description -n osc-plugin-origin
OSC plugin for for working with origin information, see `osc origin --help`.

%package -n osc-plugin-staging
Summary:        OSC plugin for the staging workflow
Group:          Development/Tools/Other
# devel-project.py needs 0.160.0 for get_request_list(withfullhistory) param.
Requires:       osc >= 0.160.0
Requires:       osclib = %{version}
BuildArch:      noarch

%description -n osc-plugin-staging
OSC plugin for the staging workflow, see `osc staging --help`.

%prep
%setup -q

%build
%make_build

%install
%make_install \
  grafana_provisioning_dir="%{_sysconfdir}/grafana/provisioning" \
  oscplugindir="%{osc_plugin_dir}" \
  VERSION="%{version}"

%pre announcer
getent passwd osrt-announcer > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-announcer" osrt-announcer
exit 0

%postun announcer
%{systemd_postun}

%pre check-source
getent passwd osrt-check-source > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-check-source" osrt-check-source
exit 0

%postun check-source
%{systemd_postun}

%pre maintenance
getent passwd osrt-maintenance > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-maintenance" osrt-maintenance
exit 0

%postun maintenance
%{systemd_postun}

%pre metrics
getent passwd osrt-metrics > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-metrics" osrt-metrics
exit 0

%postun metrics
%{systemd_postun}
# If grafana-server.service is enabled then restart it to load new dashboards.
if [ -x %{_bindir}/systemctl ] && %{_bindir}/systemctl is-enabled grafana-server ; then
  %{_bindir}/systemctl try-restart --no-block grafana-server
fi

%pre obs-operator
getent passwd osrt-obs-operator > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-obs-operator" osrt-obs-operator
exit 0

%postun obs-operator
%{systemd_postun}
if [ -x %{_bindir}/systemctl ] && %{_bindir}/systemctl is-enabled osrt-obs-operator ; then
  %{_bindir}/systemctl try-restart --no-block osrt-obs-operator
fi

%pre origin-manager
getent passwd osrt-origin-manager > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-origin-manager" osrt-origin-manager
exit 0

%postun origin-manager
%{systemd_postun}

%pre repo-checker
getent passwd osrt-repo-checker > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-repo-checker" osrt-repo-checker
exit 0

%postun repo-checker
%{systemd_postun}

%pre staging-bot
getent passwd osrt-staging-bot > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-staging-bot" osrt-staging-bot
exit 0

%postun staging-bot
%{systemd_postun}

%postun pkglistgen
%{systemd_postun}

%files
%doc README.md
%{_bindir}/osrt-biarchtool
%{_bindir}/osrt-bs_mirrorfull
%{_bindir}/osrt-bugowner
%{_bindir}/osrt-build-fail-reminder
%{_bindir}/osrt-checknewer
%{_bindir}/osrt-check_source_in_factory
%{_bindir}/osrt-check_tags_in_requests
%{_bindir}/osrt-compare_pkglist
%{_bindir}/osrt-container_cleaner
%{_bindir}/osrt-deptool
%{_bindir}/osrt-fcc_submitter
%{_bindir}/osrt-issue-diff
%{_bindir}/osrt-legal-auto
%{_bindir}/osrt-openqa-maintenance
%{_bindir}/osrt-requestfinder
%{_bindir}/osrt-totest-manager
%{_datadir}/%{source_dir}
%exclude %{_datadir}/%{source_dir}/abichecker
%exclude %{_datadir}/%{source_dir}/%{announcer_filename}
%exclude %{_datadir}/%{source_dir}/check_maintenance_incidents.py
%exclude %{_datadir}/%{source_dir}/check_source.pl
%exclude %{_datadir}/%{source_dir}/check_source.py
%exclude %{_datadir}/%{source_dir}/devel-project.py
%exclude %{_datadir}/%{source_dir}/metrics
%exclude %{_datadir}/%{source_dir}/metrics.py
%exclude %{_datadir}/%{source_dir}/metrics_release.py
%exclude %{_datadir}/%{source_dir}/origin-manager.py
%exclude %{_bindir}/osrt-staging-report
%exclude %{_datadir}/%{source_dir}/pkglistgen
%exclude %{_datadir}/%{source_dir}/pkglistgen.py
%exclude %{_datadir}/%{source_dir}/maintenance-installcheck.py
%exclude %{_datadir}/%{source_dir}/project-installcheck.py
%exclude %{_datadir}/%{source_dir}/suppkg_rebuild.py
%exclude %{_datadir}/%{source_dir}/skippkg-finder.py
%exclude %{_datadir}/%{source_dir}/osclib
%exclude %{_datadir}/%{source_dir}/osc-cycle.py
%exclude %{_datadir}/%{source_dir}/osc-origin.py
%exclude %{_datadir}/%{source_dir}/osc-staging.py
%exclude %{_datadir}/%{source_dir}/findfileconflicts
%exclude %{_datadir}/%{source_dir}/write_repo_susetags_file.pl
%dir %{_sysconfdir}/openSUSE-release-tools

%files abichecker
%{apache_sysconfdir}/vhosts.d/opensuse-abi-checker.conf.in
%{_datadir}/%{source_dir}/abichecker
%{_tmpfilesdir}/opensuse-abi-checker.conf

%files announcer
%doc %{announcer_filename}/README.asciidoc
%{_bindir}/osrt-announcer
%{apache_sysconfdir}/conf.d/%{announcer_filename}.conf.in
%{_datadir}/%{source_dir}/%{announcer_filename}
%config(noreplace) %{_sysconfdir}/openSUSE-release-tools/announcer
%config(noreplace) %{_sysconfdir}/rsyslog.d/%{announcer_filename}.conf

%files check-source
%{_bindir}/osrt-check_source
%{_datadir}/%{source_dir}/check_source.pl
%{_datadir}/%{source_dir}/check_source.py

%files maintenance
%{_bindir}/osrt-check_maintenance_incidents
%{_datadir}/%{source_dir}/check_maintenance_incidents.py

%files metrics
%{_bindir}/osrt-metrics
%{_datadir}/%{source_dir}/metrics
%exclude %{_datadir}/%{source_dir}/metrics/access
%exclude %{_datadir}/%{source_dir}/metrics/grafana/access.json
%{_datadir}/%{source_dir}/metrics.py
%{_datadir}/%{source_dir}/metrics_release.py
# To avoid adding grafana as BuildRequires since it does not live in same repo.
%dir %{_sysconfdir}/grafana
%dir %{_sysconfdir}/grafana/provisioning
%dir %{_sysconfdir}/grafana/provisioning/dashboards
%dir %{_sysconfdir}/grafana/provisioning/datasources
%{_sysconfdir}/grafana/provisioning/dashboards/%{name}.yaml
%{_sysconfdir}/grafana/provisioning/datasources/%{name}.yaml
%{_unitdir}/osrt-metrics@.service
%{_unitdir}/osrt-metrics@.timer
%{_unitdir}/osrt-metrics-release@.service
%{_unitdir}/osrt-metrics-release@.timer
%{_unitdir}/osrt-metrics-telegraf.service

%files metrics-access
%{_bindir}/osrt-metrics-access-aggregate
%{_bindir}/osrt-metrics-access-ingest
%{_datadir}/%{source_dir}/metrics/access
%{_datadir}/%{source_dir}/metrics/grafana/access.json
%{_unitdir}/osrt-metrics-access.service
%{_unitdir}/osrt-metrics-access.timer

%files obs-operator
%{_bindir}/osrt-obs_operator
%{_unitdir}/osrt-obs-operator.service
%{_unitdir}/osrt-obs-operator-origin-manager-cron.service
%{_unitdir}/osrt-obs-operator-origin-manager-cron.timer

%files origin-manager
%{_bindir}/osrt-origin-manager
%{_datadir}/%{source_dir}/origin-manager.py

%files repo-checker
%{_bindir}/osrt-project-installcheck
%{_bindir}/osrt-staging-installcheck
%{_bindir}/osrt-maintenance-installcheck
%{_bindir}/osrt-findfileconflicts
%{_bindir}/osrt-maintenance-installcheck
%{_bindir}/osrt-write_repo_susetags_file
%{_datadir}/%{source_dir}/project-installcheck.py
%{_datadir}/%{source_dir}/findfileconflicts
%{_datadir}/%{source_dir}/write_repo_susetags_file.pl

%files staging-bot
%{_bindir}/osrt-devel-project
%{_bindir}/osrt-staging-report
%{_bindir}/osrt-suppkg_rebuild
%{_datadir}/%{source_dir}/devel-project.py
%{_datadir}/%{source_dir}/suppkg_rebuild.py

%files pkglistgen
%{_bindir}/osrt-pkglistgen
%{_bindir}/osrt-skippkg-finder
%{_datadir}/%{source_dir}/pkglistgen
%{_datadir}/%{source_dir}/pkglistgen.py
%{_datadir}/%{source_dir}/skippkg-finder.py

%files -n osclib
%{_datadir}/%{source_dir}/osclib
%{osc_plugin_dir}/osclib

%files -n osc-plugin-cycle
%{_datadir}/%{source_dir}/osc-cycle.py
%{osc_plugin_dir}/osc-cycle.py

%files -n osc-plugin-origin
%{_datadir}/%{source_dir}/osc-origin.py
%{osc_plugin_dir}/osc-origin.py

%files -n osc-plugin-staging
%{_datadir}/%{source_dir}/osc-staging.py
%{osc_plugin_dir}/osc-staging.py

%changelog
