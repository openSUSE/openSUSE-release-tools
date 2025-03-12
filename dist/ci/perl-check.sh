#! /usr/bin/sh
for i in `find . | grep 'p[lm]$'`; do
  perl -Wc $i || exit $?;
done
