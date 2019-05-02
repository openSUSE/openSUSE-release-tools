#! /bin/bash

set -e

# This script is run from docker-compose within test container
usermod -u $(stat -c %u /code/LICENSE) tester

until curl http://api:3000/about 2>/dev/null ; do
  echo "waiting for OBS to be responsive..."
  # Print osc output incase of failure and container logs for debugging.
  ((c++)) && ((c==60)) && (
    curl http://api:3000/about
    exit 1
  )
  sleep 1
done

su - tester -c nosetests-2.7
