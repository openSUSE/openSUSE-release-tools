#! /bin/sh

/usr/lib/mysql/mysql-systemd-helper install
sed -e 's,127.0.0.1,0.0.0.0,' -i /etc/my.cnf
sed -e 's,server-id,skip-grant-tables,' -i /etc/my.cnf

/usr/lib/mysql/mysql-systemd-helper start &
/usr/lib/mysql/mysql-systemd-helper wait

#/usr/bin/mysqladmin -u root password 'opensuse'

chroot --userspec=wwwrun / /bin/bash -c "cd /srv/www/obs/api && DISABLE_DATABASE_ENVIRONMENT_CHECK=1 SAFETY_ASSURED=1 RAILS_ENV=production bundle exec rake db:create db:setup"
mysqladmin shutdown

sed -i -e 's,\(config.public_file_server.enabled\).*,\1 = true,; s,\(config.log_level\).*,\1 = :debug,' /srv/www/obs/api/config/environments/production.rb
rm -f /srv/www/obs/api/tmp/pids/server.pid

