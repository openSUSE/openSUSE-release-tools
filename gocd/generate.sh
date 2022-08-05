#! /bin/sh

set -e

if ! test -f yaml-config-plugin-0.9.0.jar; then
  wget https://github.com/tomzo/gocd-yaml-config-plugin/releases/download/0.9.0/yaml-config-plugin-0.9.0.jar
fi
sha1sum -c yaml-config-plugin-0.9.0.jar.sha1

for file in *.erb; do
  erb -T - $file > $(basename $file .erb)
done

grep group: *.yaml | cut -d: -f3 | sort -u | while read group; do
   case $group in
	   BCI|Factory|Leap|Admin|ALP|MicroOS|Monitors|openSUSE.Checkers|SLE15.Stagings|SLE15.Target|SLE.Checkers)
		   ;;
           *)
		   echo "Do not create new groups without being admin and knowing the consequences - found $group"
		   exit 1
   esac
done

for file in *.gocd.yaml; do
  java -jar yaml-config-plugin-0.9.0.jar syntax $file
done

