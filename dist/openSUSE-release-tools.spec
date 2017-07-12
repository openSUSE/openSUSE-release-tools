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
%define osc_plugin_dir %{_prefix}/lib/osc-plugins
%define announcer_filename factory-package-news
Name:           openSUSE-release-tools
Version:        0
Release:        0
Summary:        Tools to aid in staging and release work for openSUSE/SUSE
License:        GPL-2.0+ and MIT
Group:          Development/Tools/Other
Url:            https://github.com/openSUSE/osc-plugin-factory
# _service:tar/filename does not seem to add version like docs indicate.
Source:         %{name}.tar.xz
BuildArch:      noarch
BuildRequires:  osc
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
Requires:       python-pycurl
Requires:       python-python-dateutil
Requires:       python-pyxdg
Requires:       python-urlgrabber

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

%package totest-manager
Summary:        Manages \$product:ToTest repository
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements.
Requires:       osclib = %{version}

%description totest-manager
Manages \$product:ToTest repository workflow and openQA interaction

%package -n osclib
Summary:        Supplemental osc libraries
Group:          Development/Tools/Other
BuildArch:      noarch
# TODO Update requirements, but for now base deps.
Requires:       %{name} = %{version}

%description -n osclib
Supplemental osc libraries utilized by release tools.

%package -n osc-plugin-check_dups
Summary:        OSC plugin to check for duplicate requests
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osc

%description -n osc-plugin-check_dups
OSC plugin to check for duplicate requests, see `osc check_dups --help`.

%package -n osc-plugin-check_repo
Summary:        OSC plugin for checking request build cycles and installability
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osclib = %{version}

%description -n osc-plugin-check_repo
OSC plugin for checking request build cycles and installability, see `osc
check_repo --help`.

%package -n osc-plugin-cycle
Summary:        OSC plugin for cycle visualization
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osc

%description -n osc-plugin-cycle
OSC plugin for cycle visualization, see `osc cycle --help`.

%package -n osc-plugin-staging
Summary:        OSC plugin for the staging workflow
Group:          Development/Tools/Other
BuildArch:      noarch
Requires:       osclib = %{version}

%description -n osc-plugin-staging
OSC plugin for the staging workflow, see `osc staging --help`.

%prep
%setup -q -n "%{name}"

%build
make %{?_smp_mflags}

%check
%if 0%{?is_opensuse}
make check
%endif

%install
%make_install

# TODO Correct makefile to actually install source.
mkdir -p %{buildroot}%{_datadir}/%{source_dir}/%{announcer_filename}

%pre announcer
%service_add_pre %{announcer_filename}.service

%post announcer
%service_add_post %{announcer_filename}.service

%preun announcer
%service_del_preun %{announcer_filename}.service

%postun announcer
%service_del_postun %{announcer_filename}.service

%pre totest-manager
%service_add_pre opensuse-totest-manager.service

%post totest-manager
%service_add_post opensuse-totest-manager.service

%preun totest-manager
%service_del_preun opensuse-totest-manager.service

%postun totest-manager
%service_del_postun opensuse-totest-manager.service

%pre -n osc-plugin-check_repo
%service_add_pre opensuse-repo-checker.target

%post -n osc-plugin-check_repo
%service_add_post opensuse-repo-checker.target

%preun -n osc-plugin-check_repo
%service_del_preun opensuse-repo-checker.target

%postun -n osc-plugin-check_repo
%service_del_postun opensuse-repo-checker.target

%files
%defattr(-,root,root,-)
%doc README.asciidoc
%{_datadir}/%{source_dir}
%exclude %{_datadir}/%{source_dir}/abichecker
%exclude %{_datadir}/%{source_dir}/%{announcer_filename}
%exclude %{_datadir}/%{source_dir}/totest-manager.py
%exclude %{_datadir}/%{source_dir}/osclib
%exclude %{_datadir}/%{source_dir}/osc-check_dups.py
%exclude %{_datadir}/%{source_dir}/osc-check_repo.py
%exclude %{_datadir}/%{source_dir}/osc-cycle.py
%exclude %{_datadir}/%{source_dir}/osc-staging.py
# Should be in osc package, but ironically it is using its deprecated directory.
%dir %{osc_plugin_dir}

%files devel
%defattr(-,root,root,-)
# Non-empty for older products.
%doc README.asciidoc

%files abichecker
%{apache_sysconfdir}/vhosts.d/opensuse-abi-checker.conf.in
%{_datadir}/%{source_dir}/abichecker
%{_tmpfilesdir}/opensuse-abi-checker.conf
%{_unitdir}/opensuse-abi-checker.service

%files announcer
%defattr(-,root,root,-)
%doc %{announcer_filename}/README.asciidoc
%{apache_sysconfdir}/conf.d/%{announcer_filename}.conf.in
%{_datadir}/%{source_dir}/%{announcer_filename}
%config(noreplace) %{_sysconfdir}/rsyslog.d/%{announcer_filename}.conf
%{_unitdir}/%{announcer_filename}.service
%{_unitdir}/%{announcer_filename}.timer

%files totest-manager
%{_unitdir}/opensuse-totest-manager.service
%{_datadir}/%{source_dir}/totest-manager.py

%files -n osclib
%{_datadir}/%{source_dir}/osclib
%{osc_plugin_dir}/osclib

%files -n osc-plugin-check_dups
%{_datadir}/%{source_dir}/osc-check_dups.py
%{osc_plugin_dir}/osc-check_dups.py

%files -n osc-plugin-check_repo
%{_datadir}/%{source_dir}/osc-check_repo.py
%{osc_plugin_dir}/osc-check_repo.py
%{_unitdir}/opensuse-repo-checker.target
%{_unitdir}/opensuse-repo-checker@.service
%{_unitdir}/opensuse-repo-checker@.timer

%files -n osc-plugin-cycle
%{_datadir}/%{source_dir}/osc-cycle.py
%{osc_plugin_dir}/osc-cycle.py

%files -n osc-plugin-staging
%{_datadir}/%{source_dir}/osc-staging.py
%{osc_plugin_dir}/osc-staging.py

%changelog
