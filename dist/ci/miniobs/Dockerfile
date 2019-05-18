#!BuildTag: osrt_miniobs
FROM opensuse/leap:15.1

RUN zypper ar http://download.opensuse.org/repositories/OBS:/Server:/Unstable/openSUSE_15.1/ 'O:S:U'; \
    zypper --gpg-auto-import-keys refresh

RUN zypper install -y obs-api obs-worker obs-server \
    ca-certificates patch vim vim-data psmisc timezone \
    glibc-locale aaa_base aaa_base-extras netcat net-tools

COPY database.yml.local /srv/www/obs/api/config/database.yml

RUN /usr/lib/mysql/mysql-systemd-helper install ;\
    sed -e 's,127.0.0.1,0.0.0.0,' -i /etc/my.cnf ;\
    sed -e 's,server-id,skip-grant-tables,' -i /etc/my.cnf ;\
    /usr/lib/mysql/mysql-systemd-helper start & \
    /usr/lib/mysql/mysql-systemd-helper wait ;\
    /usr/bin/mysql -u root -e "SELECT @@version; CREATE USER 'root'@'%' IDENTIFIED BY 'opensuse'; GRANT ALL ON *.* TO 'root'@'%' WITH GRANT OPTION;" ;\
    chroot --userspec=wwwrun / /bin/bash -c "cd /srv/www/obs/api && DISABLE_DATABASE_ENVIRONMENT_CHECK=1 RAILS_ENV=production bundle exec rails db:create db:setup" ;\
    mysqladmin shutdown

COPY config.yml   /srv/www/obs/api/config/options.yml
COPY database.yml /srv/www/obs/api/config/database.yml

RUN sed -i -e 's,\(config.public_file_server.enabled\).*,\1 = true,; s,\(config.log_level\).*,\1 = :debug,' \
    /srv/www/obs/api/config/environments/production.rb
RUN rm -f /srv/www/obs/api/tmp/pids/server.pid

COPY BSConfig.pm.patch /tmp
RUN patch /usr/lib/obs/server/BSConfig.pm /tmp/BSConfig.pm.patch

