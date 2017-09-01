# https://docs.docker.com/engine/reference/builder/
# Used for TEST_SUITE=distribution and deployment to OBS.

FROM opensuse:tumbleweed
MAINTAINER Jimmy Berry <jberry@suse.com>

RUN zypper -n ref && zypper -n dup && zypper -n in --no-recommends \
  obs-service-download_files \
  obs-service-format_spec_file \
  obs-service-obs_scm \
  obs-service-set_version \
  obs-service-source_validator \
  rpm-build \
  apache-rpm-macros \
  apache2-devel \
  libxml2-tools \
  make \
  osc \
  python-PyYAML \
  python-cmdln \
  python-colorama \
  python-httpretty \
  python-lxml \
  python-mock \
  python-nose \
  python-python-dateutil \
  python-pyxdg \
  rsyslog

RUN mkdir -p /usr/src/app
WORKDIR /usr/src/app

COPY . /usr/src/app

# Already included in target, but simulate separate container image.
ADD dist/ci/obs-deploy /usr/bin/
ADD dist/ci/osc-init /usr/bin/
