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
  if test -f /code/travis.settings; then
    COVER_ARGS="--with-coverage --cover-package=. --cover-inclusive"
  fi
  run_as_tester nosetests $COVER_ARGS -c .noserc -s $file
done

set -x

if test -f /code/travis.settings; then
  source /code/travis.settings
  # ignore if coveralls was not setup for the repo/branch
  run_as_tester TRAVIS_JOB_ID=$TRAVIS_JOB_ID TRAVIS_BRANCH=$TRAVIS_BRANCH TRAVIS_PULL_REQUEST=$TRAVIS_PULL_REQUEST TRAVIS=yes coveralls || true
fi

