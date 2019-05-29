#!/bin/sh

set -ex

run_as_tester flake8
run_as_tester ./dist/ci/flake-extra
