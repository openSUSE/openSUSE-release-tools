#!BuildTag: osrt_miniobs
FROM opensuse/leap:15.3

RUN zypper ar http://download.opensuse.org/repositories/OBS:/Server:/Unstable/15.3/ 'O:S:U'; \
    zypper --gpg-auto-import-keys refresh

RUN zypper install -y obs-api obs-worker obs-server \
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

