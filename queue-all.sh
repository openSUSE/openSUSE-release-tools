rm -f missingdeps
wget "http://gitorious.org/opensuse/package-lists/blobs/raw/master/output/opensuse/missingdeps"
list=`osc api /search/package/?match='@project="openSUSE:Factory"' | grep "<devel project=" | sed -e 's,.*project=",,; s,".*,,' | sort -u`
users=`mktemp`
dir=reports-`date +%F`
mkdir $dir
( for i in $list ; do 
  echo "query '$i'" >&2
  osc meta prj $i 
  osc api "/search/package/?match=@project='$i'"
done | grep '<person.*role="maintainer"'
) | sed -e 's,.*userid=",,; s,".*,,'  | sort -u > $users
for i in `cat $users`; do 
  echo "generate $i"
  perl generate-reminder.pl $i > $dir/$i.txt
done
echo "DONE $dir"
