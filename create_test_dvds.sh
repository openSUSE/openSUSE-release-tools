#! /bin/bash

set -e

if ! test -d co; then
  echo "you need to call this in a directory with a co directory containting osc checkouts with the staging prjs" 
  exit 1
fi

CODIR=$PWD
SCRIPTDIR=`dirname "$0"`

function regenerate_pl() {
    prj=$1
    shift;

    suffix=$1
    shift;
    
    : > tc
    for i in "$@"; do
	echo "repo $i 0 solv $i.solv" >> tc
    done
    cat $SCRIPTDIR/create_test_dvd-$suffix.testcase >> tc

    out=$(mktemp)
    testsolv -r tc | sed -e 's,^install \(.*\)-[^-]*-[^-]*\.[^-\.]*@.*,\1,' > $out
    
    p=$(mktemp)
    tdir=$CODIR/co/$prj/Test-DVD-x86_64
    pushd $tdir > /dev/null
    osc up
    popd > /dev/null
    sed -n -e '1,/BEGIN-PACKAGELIST/p' $tdir/PRODUCT-x86_64.kiwi > $p
    for i in $(cat $out); do
	echo "<repopackage name='$i'/>" >> $p
    done
    sed -n -e '/END-PACKAGELIST/,$p' $tdir/PRODUCT-x86_64.kiwi >> $p
    xmllint --format $p -o $tdir/PRODUCT-x86_64.kiwi
    rm $p $out
    pushd $tdir > /dev/null
    if ! cmp -s .osc/PRODUCT-x86_64.kiwi PRODUCT-x86_64.kiwi; then
      osc ci -m "auto update"
    fi
    popd > /dev/null
}

function sync_prj() {
    prj=$1
    dir=$2
    mkdir -p $dir
    perl $SCRIPTDIR/bs_mirrorfull --nodebug https://build.opensuse.org/build/$prj/x86_64 $dir
    rpms2solv $dir/*.rpm > $dir.solv
}

sync_prj openSUSE:Factory:Rings:0-Bootstrap/standard/ bootstrap
sync_prj openSUSE:Factory:Rings:1-MinimalX/standard minimalx

regenerate_pl openSUSE:Factory:Rings:1-MinimalX 1 bootstrap minimalx

sync_prj openSUSE:Factory:Rings:2-TestDVD/standard testdvd
regenerate_pl openSUSE:Factory:Rings:2-TestDVD 2 bootstrap minimalx testdvd

sync_prj openSUSE:Factory:Staging:A/standard staging_A
regenerate_pl "openSUSE:Factory:Staging:A" 1 staging_A

sync_prj openSUSE:Factory:Staging:A:DVD/standard staging_A-dvd
regenerate_pl "openSUSE:Factory:Staging:A:DVD" 2 staging_A staging_A-dvd

for l in B C D E F G H I J; do
  sync_prj openSUSE:Factory:Staging:$l/bootstrap_copy "staging_$l-bc"
  sync_prj openSUSE:Factory:Staging:$l/standard staging_$l
  regenerate_pl "openSUSE:Factory:Staging:$l" 1 "staging_$l-bc" staging_$l
done

projects=$(osc api /search/project/id?match='starts-with(@name,"openSUSE:Factory:Staging")' | grep :DVD | cut -d\' -f2)
for prj in openSUSE:Factory:Rings:2-TestDVD $projects; do
  perl $SCRIPTDIR/rebuildpacs.pl $prj standard x86_64
done

for l in B C D E F G H I; do
  sync_prj openSUSE:Factory:Staging:$l:DVD/standard "staging_$l-dvd"
  regenerate_pl "openSUSE:Factory:Staging:$l:DVD" 2 "staging_$l-bc" staging_$l "staging_$l-dvd"
done
