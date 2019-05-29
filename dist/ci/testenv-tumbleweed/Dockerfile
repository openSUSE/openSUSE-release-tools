#!BuildTag: osrt_testenv_tumbleweed
FROM opensuse/tumbleweed

# make sure we see osc regressions earlier than it hitting tumbleweed
RUN zypper -n ar http://download.opensuse.org/repositories/openSUSE:/Tools/openSUSE_Factory/ openSUSE:Tools
RUN zypper --gpg-auto-import-keys ref

RUN useradd tester -d /code/tests/home

RUN zypper in -y osc python3-nose python3-httpretty python3-pyxdg python3-PyYAML \
   python3-pika python3-mock python3-cmdln python3-lxml python3-python-dateutil python3-colorama \
   python3-influxdb python3-coverage python3-coveralls libxml2-tools curl python3-flake8 \
   vim vim-data strace git sudo patch openSUSE-release openSUSE-release-ftp

COPY run_as_tester /usr/bin
# OBS does not know about executable files, so we need to tweak it manually
RUN chmod a+x /usr/bin/run_as_tester
