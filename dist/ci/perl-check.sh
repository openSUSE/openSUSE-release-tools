#! /usr/bin/sh
for i in `find . | grep 'p[lm]$'`; do
  perl -I bs_copy -Wc $i || exit $?;
done
