FROM registry.opensuse.org/opensuse/leap:15.0

RUN useradd tester -d /code

RUN zypper in -y osc python2-nose python2-httpretty python2-pyxdg python2-PyYAML \
   python2-pika python2-mock python2-cmdln python2-lxml python2-python-dateutil \
   vim vim-data

