#! /bin/sh

set -e

if ! test -f yaml-config-plugin-0.9.0.jar; then
  wget https://github.com/tomzo/gocd-yaml-config-plugin/releases/download/0.9.0/yaml-config-plugin-0.9.0.jar
fi

for file in *.erb; do
  erb -T - $file > $(basename $file .erb)
done

for file in *.gocd.yaml; do
  java -jar yaml-config-plugin-0.9.0.jar syntax $file
done
