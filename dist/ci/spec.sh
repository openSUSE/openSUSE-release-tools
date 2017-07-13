#!/bin/bash
# Adapted from: https://github.com/openSUSE/snapper/blob/master/.travis.tumbleweed.sh.

set -e -x

make
make package

# Validate package.
(cd dist/package && /usr/lib/obs/service/source_validator)

# Build package (--nocheck as test suite runs separately).
cp dist/package/* /usr/src/packages/SOURCES/
rpmbuild --nocheck -bb -D "jobs `nproc`" dist/package/*.spec

# Install to test scripts.
rpm -iv --force --nodeps /usr/src/packages/RPMS/*/*.rpm

# Ensure the staging plugin starts.
cat << eom > ~/.oscrc
[general]
[https://api.opensuse.org]
user = example
pass = example
eom

osc staging --version

# Upgrade and uninstall to test scripts.
rpm -Uv --force --nodeps /usr/src/packages/RPMS/*/*.rpm
# get the plain package names and remove all packages at once
rpm -ev --nodeps `rpm -q --qf '%{NAME} ' -p /usr/src/packages/RPMS/**/*.rpm`
