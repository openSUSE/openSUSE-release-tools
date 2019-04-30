#! /bin/bash

set -e

cat /etc/passwd

# This script is run from docker-compose within test container
usermod -u $(stat -c %u /code/LICENSE) tester

export HOME=/tmp
chroot --userspec=tester / /bin/bash -c "cd /code && nosetests-2.7"

