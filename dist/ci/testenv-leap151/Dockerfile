#!BuildTag: osrt_testenv_leap151
FROM opensuse/leap:15.1

RUN useradd tester -d /code/tests/home

RUN zypper in -y osc python3-nose python3-httpretty python3-pyxdg python3-PyYAML \
   python3-pika python3-mock python3-cmdln python3-lxml python3-python-dateutil python3-colorama \
   python3-influxdb python3-coverage libxml2-tools curl \
   vim vim-data strace git sudo patch

COPY osc-hotpatch.diff /tmp
RUN patch /usr/lib/python3.6/site-packages/osc/core.py /tmp/osc-hotpatch.diff
COPY run_as_tester /usr/bin
