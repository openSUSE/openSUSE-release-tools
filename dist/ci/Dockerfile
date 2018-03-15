# https://docs.docker.com/engine/reference/builder/
# Used for TEST_SUITE=distribution and deployment to OBS.

FROM opensuse/tumbleweed
MAINTAINER Jimmy Berry <jberry@suse.com>

# https://github.com/openSUSE/obs-service-set_version/issues/44 python-packaging
# git for extracting remote, but a more generalized approach would be nice
RUN zypper -n ref && zypper -n dup && zypper -n install \
  build \
  git \
  obs-service-* \
  osc \
  python-packaging \
  sudo

# `osc build` directories that are effective to cache:
# - /var/tmp/build-root
# - /var/tmp/osbuild-packagecache
VOLUME /var/tmp

WORKDIR /usr/src
RUN mkdir -p /usr/src/target
COPY . /usr/src/target

# Already included in target, but simulate separate container image.
ADD dist/ci/obs-build-target /usr/bin/
ADD dist/ci/obs-deploy /usr/bin/
ADD dist/ci/osc-init /usr/bin/
