#! /bin/bash

set -e

if ! test -d co; then
	echo "you need to call this in a directory with a co directory containting osc checkouts with the staging prjs" 
	exit 1
fi

# give it target Factory by default then will not breaks current operation
if [ $# -eq 0 ]; then
    targets='Factory'
    arch='x86_64'
else
    for arg in $@;do
        if [ "$arg" = "x86_64" -o "$arg" = "ppc64le" ]; then
            arch="$arg"
        else
            targets+="$arg"
        fi
    done
fi

CODIR=$PWD
SCRIPTDIR=`dirname "$0"`

function regenerate_pl() {
    prj=$1
    shift;

    target=$1
    shift;

    suffix=$1
    shift;

    arch=${@: -1}

    tcfile=tc.$target.$suffix.$1
    : > $tcfile
    for i in "$@"; do
	if [ "$i" != "$arch" ];then
		echo "repo $i 0 solv $i.solv" >> $tcfile
	fi
    done
    cpp -E -U__ppc64__ -U__x86_64__ -D__$arch\__ $SCRIPTDIR/create_test_$target\_dvd-$suffix.testcase >> $tcfile

    out=$(mktemp)
    testsolv -r $tcfile > $out
    if grep ^problem $out ; then
         return
    fi
    sed -i -e 's,^install \(.*\)-[^-]*-[^-]*\.[^-\.]*@.*,\1,' $out
    
    p=$(mktemp)
    tdir=$CODIR/co/$prj/Test-DVD-$arch
    if [ ! -d "$tdir" ]; then
	mkdir -p "$tdir"
	osc co -o "$tdir" "$prj" Test-DVD-$arch
    fi
    pushd $tdir > /dev/null
    osc up
    popd > /dev/null
    sed -n -e '1,/BEGIN-PACKAGELIST/p' $tdir/PRODUCT-$arch.kiwi > $p
    for i in $(cat $out); do
	echo "<repopackage name='$i'/>" >> $p
    done
    sed -n -e '/END-PACKAGELIST/,$p' $tdir/PRODUCT-$arch.kiwi >> $p
    xmllint --format $p -o $tdir/PRODUCT-$arch.kiwi
    rm $p $out
    pushd $tdir > /dev/null
    if ! cmp -s .osc/PRODUCT-$arch.kiwi PRODUCT-$arch.kiwi; then
      osc ci -m "auto update"
    fi
    popd > /dev/null
}

function sync_prj() {
    prj=$1
    dir=$2
    arch=$3
    mkdir -p $dir
    perl $SCRIPTDIR/bs_mirrorfull --nodebug https://build.opensuse.org/build/$prj/$arch $dir
    if [ "$dir" -nt "$dir.solv" ]; then
	    local start=$SECONDS
	    rpms2solv $dir/*.rpm > $dir.solv
	    echo "creating ${dir}.solv took $((SECONDS-$start))s"
    fi
}

function start_creating() {
    for target in "$targets"; do
        # Rings part
        sync_prj openSUSE:$target:Rings:0-Bootstrap/standard/ $target-bootstrap $arch
        sync_prj openSUSE:$target:Rings:1-MinimalX/standard $target-minimalx $arch

        regenerate_pl openSUSE:$target:Rings:1-MinimalX $target 1 $target-bootstrap $target-minimalx $arch

        #we don't have all architectures yet.
        projects=$(osc api /search/project/id?match="starts-with(@name,\"openSUSE:$target:Staging\")" | grep name | cut -d\' -f2)
        if [ "$arch" = "x86_64" ];then
                sync_prj openSUSE:$target:Rings:2-TestDVD/standard $target-testdvd $arch
                regenerate_pl openSUSE:$target:Rings:2-TestDVD $target 2 $target-bootstrap $target-minimalx $target-testdvd $arch
                $projects+=" openSUSE:$target:Rings:2-TestDVD"
        fi

        for prj in $projects; do
            l=$(echo $prj | cut -d: -f4)
            use_bc="staging_$target:$l-bc"
            if [ "$l" = "A" -o "$l" = "B" ]; then
                use_bc=
            fi
            if [[ $prj =~ ^openSUSE.+:[A-Z]$ ]]; then

                echo "Checking $target:$l"
                if [ -n "$use_bc" ]; then
                    sync_prj openSUSE:$target:Staging:$l/bootstrap_copy "staging_$target:$l-bc" $arch
                fi
                sync_prj openSUSE:$target:Staging:$l/standard staging_$target:$l $arch
                regenerate_pl "openSUSE:$target:Staging:$l" $target 1 $use_bc staging_$target:$l $arch
            fi

            if [[ ( $prj =~ :DVD ) || ( $prj =~ Rings:2-TestDVD ) ]]; then
                perl $SCRIPTDIR/rebuildpacs.pl $prj standard $arch
            fi

            if [[ $prj =~ ^openSUSE.+:[A-Z]:DVD$ ]]; then
                sync_prj openSUSE:$target:Staging:$l:DVD/standard "staging_$target:$l-dvd" $arch
                regenerate_pl "openSUSE:$target:Staging:$l:DVD" $target 2 $use_bc staging_$target:$l "staging_$target:$l-dvd" $arch
            fi
        done
    done
}

# call main function
start_creating $targets $arch

