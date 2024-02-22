#! /bin/sh

set -e

version=0.13.0
if ! test -f yaml-config-plugin-$version.jar; then
  wget https://github.com/tomzo/gocd-yaml-config-plugin/releases/download/$version/yaml-config-plugin-$version.jar
fi
sha1sum -c yaml-config-plugin-$version.jar.sha1

for file in *.erb; do
  erb -T - $file > $(basename $file .erb)
done

grep group: *.yaml | cut -d: -f3 | sort -u | while read group; do
  case $group in
    BCI|Factory|Leap|Admin|MicroOS|Monitors|openSUSE.Checkers|SLE15.Stagings|SLE15.Target|SLE.Checkers|ALP.Stagings|ALP.Target|ALP.Checkers|openSUSE.Legal|SUSE.Legal)
    ;;
  *)
    echo "Do not create new groups without being admin and knowing the consequences - found $group"
    exit 1
  esac
done

for file in *.gocd.yaml; do
  java -jar yaml-config-plugin-$version.jar syntax $file
done

