#!BuildTag: osrt_miniobs
FROM opensuse/leap:16.0

RUN zypper ar http://download.opensuse.org/repositories/OBS:/Server:/Unstable/16.0/ 'O:S:U'; \
    zypper --gpg-auto-import-keys refresh

RUN zypper install -y ruby4.0 -ruby3.4 obs-api obs-worker obs-server \
    ca-certificates patch vim vim-data psmisc timezone \
    glibc-locale aaa_base aaa_base-extras netcat net-tools \
    obs-service-recompress obs-service-format_spec_file

COPY database.yml.local /srv/www/obs/api/config/database.yml

COPY prepare.sh /
RUN bash -xe /prepare.sh

COPY config.yml   /srv/www/obs/api/config/options.yml
COPY database.yml /srv/www/obs/api/config/database.yml

COPY BSConfig.pm.patch /tmp
RUN patch /usr/lib/obs/server/BSConfig.pm /tmp/BSConfig.pm.patch

