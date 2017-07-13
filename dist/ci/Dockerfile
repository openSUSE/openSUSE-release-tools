# https://docs.docker.com/engine/reference/builder/

FROM boombatower/opensuse:tumbleweed
MAINTAINER Jimmy Berry <jberry@suse.com>

RUN zypper ref && zypper -n in --no-recommends \
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
