#
# Copyright (c) 2020 SUSE LLC
#
# This file is under MIT license


Name:           blowfish
Version:        1
Release:        0
Summary:        Blowfish
License:        GPL-2.0-only
URL:            https://github.com/openSUSE/cockpit-wicked
Source:         blowfish-1.tar.gz
Patch1:         patch1.patch
Patch2:         patch2.patch
BuildArch:      noarch

%prep
%autopatch

%changelog
