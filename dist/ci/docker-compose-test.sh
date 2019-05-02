#! /bin/bash

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

run_as_tester nosetests-2.7
