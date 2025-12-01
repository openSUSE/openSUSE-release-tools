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
%define services osrt-slsa.target osrt-relpkggen@.timer osrt-relpkggen@.service osrt-pkglistgen@.timer osrt-pkglistgen@.service
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

BuildRequires:  sysuser-shadow
BuildRequires:  sysuser-tools

Requires:       python3-PyYAML
Requires:       python3-cmdln
Requires:       python3-colorama
Requires:       python3-lxml
# issue-diff.py, legal-auto.py, and openqa-maintenance.py
Requires:       python3-pycurl
Requires:       python3-python-dateutil
Requires:       python3-pyxdg
Requires:       python3-requests
%if 0%{?suse_version} <= 1500
# typing extensions are needed on SLE & Leap
Requires:       python3-typing_extensions
# Backport for py 3.6
Requires:       python3-dataclasses
%endif

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
Requires:       obs-scm-bridge
Requires:       obs-service-download_files
Requires:       obs-service-source_validator
Requires:       osclib = %{version}
Requires:       perl-Text-Diff
Requires(pre):  shadow
BuildArch:      noarch

%description check-source
Check source review bot that performs basic source analysis and assigns reviews.

%package docker-publisher
Summary:        Docker image publishing bot
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       python3-requests
Requires:       python3-lxml
Requires(pre):  shadow

%description docker-publisher
A docker image publishing bot which regularly pushes built docker images from
several sources (Repo, URL) to several destinations (git, Docker registries)

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
Requires:       influxdb2
Requires:       python3-influxdb-client
Requires:       telegraf
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

%package publish-distro
Summary:        Tool for publishing ftp-stage to ftp-prod
Group:          Development/Tools/Other
Requires:       rsync
Requires(pre):  shadow
BuildArch:      noarch

%description publish-distro
publish_distro tool and related configuration in publish_distro to rsync
content from OBS to ftp-stage/ftp-prod on pontifex host.

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
Requires:       zstd
%if 0%{?suse_version} > 1500
# slfo-packagelist-uploader.py
Requires:       python3-GitPython
%endif
# we use the same user as repo-checker
PreReq:         openSUSE-release-tools-repo-checker
BuildArch:      noarch

%description pkglistgen
Generates package lists based on 000package-groups and puts them
in 000product, resp 000release-packages

%package slsa-build-service
Summary:        Build service
Group:          Development/Tools/Other
# TODO Update requirements, but for now base deps.
Requires:       %{name} = %{version}
Requires:       openSUSE-release-tools-pkglistgen
%sysusers_requires
Recommends:     logrotate
BuildArch:      noarch

%description slsa-build-service
Service to run repo-checker and pkglistgen.

%package -n osclib
Summary:        Supplemental osc libraries
Group:          Development/Tools/Other
# TODO Update requirements, but for now base deps.
Requires:       %{name} = %{version}
Requires:       osc >= 0.165.1
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

%package -n osc-plugin-pcheck
Summary:        OSC plugin to support devel project maintainers
Group:          Development/Tools/Other
Requires:       osc >= 0.165.1
Requires:       osclib = %{version}
BuildArch:      noarch

%description -n osc-plugin-pcheck
OSC plugin for devel project maintainers. Helps them check the submit
state (done, todo, missing links) of a devel project to the parent project.
See 'osc pcheck --help'

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
# slfo-packagelist-uploader requires python-GitPython but 15.6 providing
# python311-GitPython only, therefore do not ship slfo-packagelist-uploader
# in 15.6 since python3-GitPython is not available
# metrics.py requires python3-infludbdb-client which is unavailable on 15.6
%if 0%{?suse_version} <= 1500
rm slfo-packagelist-uploader.py metrics.py
%endif

%build
%make_build
%sysusers_generate_pre slsa/osrt-slsa-user.conf %{name} %{name}.conf

%install
%make_install \
  grafana_provisioning_dir="%{_sysconfdir}/grafana/provisioning" \
  oscplugindir="%{osc_plugin_dir}" \
  VERSION="%{version}"

install -Dpm0644 slsa/osrt-slsa-user.conf %{buildroot}%{_sysusersdir}/%{name}.conf

%pre -f %{name}.pre
%service_add_pre %{name}.service

%pre announcer
getent passwd osrt-announcer > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-announcer" osrt-announcer
exit 0

%pre check-source
getent passwd osrt-check-source > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-check-source" osrt-check-source
exit 0

%pre docker-publisher
getent passwd osrt-docker-publisher > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-docker-publisher" osrt-docker-publisher
exit 0

%pre maintenance
getent passwd osrt-maintenance > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-maintenance" osrt-maintenance
exit 0

%pre metrics
getent passwd osrt-metrics > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-metrics" osrt-metrics
exit 0

%postun metrics
%systemd_postun osrt-metrics-telegraf.service
# If grafana-server.service is enabled then restart it to load new dashboards.
if [ -x %{_bindir}/systemctl ] && %{_bindir}/systemctl is-enabled grafana-server ; then
  %{_bindir}/systemctl try-restart --no-block grafana-server
fi

%pre origin-manager
getent passwd osrt-origin-manager > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-origin-manager" osrt-origin-manager
exit 0

%pre repo-checker
getent passwd osrt-repo-checker > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-repo-checker" osrt-repo-checker
exit 0

%pre slsa-build-service
%service_add_pre %{services}

%post slsa-build-service
%service_add_post %{services}

%preun slsa-build-service
%service_del_preun %{services}

%postun slsa-build-service
%service_del_postun_with_restart %{services}

%pre staging-bot
getent passwd osrt-staging-bot > /dev/null || \
  useradd -r -m -s /sbin/nologin -c "user for openSUSE-release-tools-staging-bot" osrt-staging-bot
exit 0

%files
%doc README.md
%{_bindir}/osrt-biarchtool
%{_bindir}/osrt-bugowner
%{_bindir}/osrt-build-fail-reminder
%{_bindir}/osrt-checknewer
%{_bindir}/osrt-check_bugowner
%{_bindir}/osrt-check_tags_in_requests
%{_bindir}/osrt-compare_pkglist
%{_bindir}/osrt-container_cleaner
%{_bindir}/osrt-deptool
%{_bindir}/osrt-devel_update
%{_bindir}/osrt-fcc_submitter
%{_bindir}/osrt-issue-diff
%{_bindir}/osrt-legal-auto
%{_bindir}/osrt-openqa-maintenance
%{_bindir}/osrt-git-openqa-maintenance
%{_bindir}/osrt-repo2fileprovides
%{_bindir}/osrt-requestfinder
%{_bindir}/osrt-totest-manager
%{_datadir}/%{source_dir}
%exclude %{_datadir}/%{source_dir}/abichecker
%exclude %{_datadir}/%{source_dir}/%{announcer_filename}
%exclude %{_datadir}/%{source_dir}/check_maintenance_incidents.py
%exclude %{_datadir}/%{source_dir}/check_source.py
%exclude %{_datadir}/%{source_dir}/devel-project.py
%exclude %{_datadir}/%{source_dir}/docker_publisher.py
%exclude %{_datadir}/%{source_dir}/docker_registry.py
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
%exclude %{_datadir}/%{source_dir}/slfo-packagelist-uploader.py
%exclude %{_datadir}/%{source_dir}/osclib
%exclude %{_datadir}/%{source_dir}/osc-cycle.py
%exclude %{_datadir}/%{source_dir}/osc-origin.py
%exclude %{_datadir}/%{source_dir}/osc-pcheck.py
%exclude %{_datadir}/%{source_dir}/osc-staging.py
%exclude %{_datadir}/%{source_dir}/publish_distro
%exclude %{_datadir}/%{source_dir}/findfileconflicts
%exclude %{_datadir}/%{source_dir}/generate-release-packages
%exclude %{_datadir}/%{source_dir}/verify-build-and-generatelists
%exclude %{_datadir}/%{source_dir}/verify-repo-built-successful.py
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
%if 0%{?suse_version} > 1500
%{_bindir}/osrt-slfo-packagelist-uploader
%{_datadir}/%{source_dir}/slfo-packagelist-uploader.py
%endif
%{_datadir}/%{source_dir}/check_source.py

%files docker-publisher
%{_bindir}/osrt-docker_publisher
%{_datadir}/%{source_dir}/docker_publisher.py
%{_datadir}/%{source_dir}/docker_registry.py
%{_unitdir}/osrt-docker-publisher.service
%{_unitdir}/osrt-docker-publisher.timer

%files slsa-build-service
%{_bindir}/osrt-generate-release-packages
%{_bindir}/osrt-verify-build-and-generatelists
%{_datadir}/%{source_dir}/generate-release-packages
%{_datadir}/%{source_dir}/verify-build-and-generatelists
%{_datadir}/%{source_dir}/verify-repo-built-successful.py
%{_sysconfdir}/openSUSE-release-tools/ibsapi
%{_sysusersdir}/%{name}.conf
%{_unitdir}/osrt-pkglistgen@.service
%{_unitdir}/osrt-pkglistgen@.timer
%{_unitdir}/osrt-relpkggen@.service
%{_unitdir}/osrt-relpkggen@.timer
%{_unitdir}/osrt-slsa.target
%config(noreplace) %attr(0640,osrt-slsa,osrt-slsa) %{_sysconfdir}/openSUSE-release-tools/oscrc
%config(noreplace) %{_sysconfdir}/logrotate.d/%{name}
%dir %attr(755,osrt-slsa,osrt-slsa) %{_localstatedir}/log/openSUSE-release-tools/
%dir %attr(750,osrt-slsa,osrt-slsa) %{_sharedstatedir}/osrt-slsa
%dir %attr(750,osrt-slsa,osrt-slsa) %{_sharedstatedir}/osrt-slsa/pkglistgen
%dir %attr(750,osrt-slsa,osrt-slsa) %{_sharedstatedir}/osrt-slsa/relpkggen

%files maintenance
%{_bindir}/osrt-check_maintenance_incidents
%{_datadir}/%{source_dir}/check_maintenance_incidents.py

%files metrics
%{_datadir}/%{source_dir}/metrics
%exclude %{_datadir}/%{source_dir}/metrics/access
%exclude %{_datadir}/%{source_dir}/metrics/grafana/access.json
%if 0%{?suse_version} > 1500
%{_bindir}/osrt-metrics
%{_datadir}/%{source_dir}/metrics.py
%else
%exclude %{_bindir}/osrt-metrics
%exclude %{_datadir}/%{source_dir}/metrics.py
%endif
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

%files origin-manager
%{_bindir}/osrt-origin-manager
%{_datadir}/%{source_dir}/origin-manager.py

%files publish-distro
%{_bindir}/osrt-publish_distro
%{_datadir}/%{source_dir}/publish_distro

%files repo-checker
%{_bindir}/osrt-project-installcheck
%{_bindir}/osrt-staging-installcheck
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

%files -n osc-plugin-pcheck
%{_datadir}/%{source_dir}/osc-pcheck.py
%{osc_plugin_dir}/osc-pcheck.py

%files -n osc-plugin-origin
%{_datadir}/%{source_dir}/osc-origin.py
%{osc_plugin_dir}/osc-origin.py

%files -n osc-plugin-staging
%{_datadir}/%{source_dir}/osc-staging.py
%{osc_plugin_dir}/osc-staging.py

%changelog
