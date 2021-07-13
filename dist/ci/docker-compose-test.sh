#!/bin/bash

set -e

until curl http://api:3000/about 2>/dev/null ; do
  echo "waiting for OBS to be responsive..."
  # Print osc output incase of failure and container logs for debugging.
  ((c++)) && ((c==60)) && (
    curl http://api:3000/about
    exit 1
  )
  sleep 1
done

cd /code
for file in tests/*_tests.py; do
  if ! test -f /code/.without-coverage; then
    COVER_ARGS="--cov=. --cov-append --cov-report=xml"
  else
    COVER_ARGS="--no-cov"
  fi
  # TODO: Review bot test failed without log-level set to debug
  # TODO: due to memoize tests cannot be run together, otherwise it start failing
  run_as_tester pytest $COVER_ARGS --log-level=DEBUG $file
done

set -x

