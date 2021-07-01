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
    COVER_ARGS="--with-coverage --cover-xml --cover-package=. --cover-inclusive --cover-no-print"
  fi
  echo "running tests from $file..."
  run_as_tester nosetests $COVER_ARGS -c .noserc -s $file
done

set -x

