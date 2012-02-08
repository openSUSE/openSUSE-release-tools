list=`osc api /status/project/openSUSE:Factory | grep '<develpack' | sed -e 's,.*proj=",,; s,".*,,' | sort -u`
users=`mktemp`
mbox=`mktemp`
( for i in $list ; do 
  osc meta prj $i | grep '<person.*role="maintainer"'
done
osc api /status/project/openSUSE:Factory | grep '<person.*role="maintainer"'
) | sed -e 's,.*userid=",,; s,".*,,'  | sort -u > $users
tuser=`mktemp`
tail -f $mbox &
for i in `cat $users`; do 
  osc meta user $i  > $tuser
  perl generate-reminder.pl $i >> $mbox
done
echo "DONE $mbox"
