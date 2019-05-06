#!/bin/sh

set -ex

zypper in -y python3-flake8
run_as_tester flake8
run_as_tester ./dist/ci/flake-extra
