#! /bin/bash

set -e
shopt -s nullglob

if ! test -d co; then
	echo "you need to call this in a directory with a co directory containting osc checkouts with the staging prjs" 
	exit 1
fi

dryrun=

# give it target Factory by default then will not breaks current operation
if [ $# -eq 0 ]; then
    targets='Factory'
    arch='x86_64'
    has_ring_0='yes'
    has_ring_1='yes'
    has_ring_2='yes'
    has_staging='yes'
else
    for arg in $@;do
        if [ "$arg" = "x86_64" -o "$arg" = "ppc64le" ]; then
            arch="$arg"
        elif [ "$arg" = "has_ring_all" ]; then
            has_ring_0='yes'
            has_ring_1='yes'
            has_ring_2='yes'
        elif [ "$arg" = "has_ring_0" ]; then
            has_ring_0='yes'
        elif [ "$arg" = "has_ring_1" ]; then
            has_ring_0='yes'
            has_ring_1='yes'
        elif [ "$arg" = "has_ring_2" ]; then
            has_ring_0='yes'
            has_ring_1='yes'
            has_ring_2='yes'
        elif [ "$arg" = "has_staging" ]; then
            has_staging='yes'
        elif [ "$arg" = "dryrun" ]; then
	    dryrun='yes'
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
    ERRPKG=""
    if grep ^problem $out ; then
         # invalidate the kiwi file - ensuring it is not being built while we can't calculate it
         ERRPKG="CREATE_TEST_DVD_PROBLEM"
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
    for i in $(cat $out) $ERRPKG; do
	echo "<repopackage name='$i'/>" >> $p
    done
    sed -n -e '/END-PACKAGELIST/,$p' $tdir/PRODUCT-$arch.kiwi >> $p
    xmllint --format $p -o $tdir/PRODUCT-$arch.kiwi
    rm $p $out
    pushd $tdir > /dev/null
    if ! cmp -s .osc/PRODUCT-$arch.kiwi PRODUCT-$arch.kiwi; then
	if [ "$dryrun" = 'yes' ]; then
	    diff -u .osc/PRODUCT-$arch.kiwi PRODUCT-$arch.kiwi || :
	else
	    osc ci -m "auto update"
	fi
    fi
    popd > /dev/null
}

function sync_prj() {
    prj=$1
    dir=$2
    arch=$3
    mkdir -p $dir
    perl $SCRIPTDIR/bs_mirrorfull --nodebug https://api.opensuse.org/public/build/$prj/$arch $dir
    if [ "$dir" -nt "$dir.solv" ]; then
        rpms=($dir/*.rpm)
        if [ "${#rpms[@]}" -gt 0 ]; then
            local start=$SECONDS
            rpms2solv "${rpms[@]}" > $dir.solv
            echo "creating ${dir}.solv took $((SECONDS-$start))s"
        else
            echo "cannot find any rpm file in ${dir}"
            return
        fi
    fi
}

function start_creating() {
    for target in "$targets"; do
        echo "Start checking $target $arch"
        # Rings part
        if [ "$has_ring_0" = "yes" ]; then
            sync_prj openSUSE:$target:Rings:0-Bootstrap/standard/ $target-bootstrap-$arch $arch
        fi
        if [ "$has_ring_1" = "yes" ]; then
            sync_prj openSUSE:$target:Rings:1-MinimalX/standard $target-minimalx-$arch $arch
            regenerate_pl openSUSE:$target:Rings:1-MinimalX $target 1 $target-bootstrap-$arch $target-minimalx-$arch $arch
        fi
        if [ "$has_ring_2" = "yes" ]; then
            sync_prj openSUSE:$target:Rings:2-TestDVD/standard $target-testdvd-$arch $arch
            regenerate_pl openSUSE:$target:Rings:2-TestDVD $target 2 $target-bootstrap-$arch $target-minimalx-$arch $target-testdvd-$arch $arch
            if [ "$dryrun" != 'yes' ]; then
                perl $SCRIPTDIR/rebuildpacs.pl openSUSE:$target:Rings:2-TestDVD standard $arch
            fi
        fi

        # Staging Project part
        if [ "$has_staging" = "yes" ]; then
            projects=$(osc api "/search/project/id?match=starts-with(@name,\"openSUSE:$target:Staging\")" | grep name | cut -d\' -f2)

            for prj in $projects; do
                l=$(echo $prj | sed 's/^openSUSE.\+[:]Staging/Staging/g' | cut -d: -f2)
                if [[ $prj =~ ^openSUSE.+:[A-Z]$ ]] || [[ $prj =~ ^openSUSE.+:[A-Z]:DVD$ ]]; then
                    # if the testdvd build is disabled, do not regenerate the pacakges list and go to next staging project
                    testdvd_disabled=$(osc api "/build/openSUSE:$target:Staging:$l/_result?view=summary&package=Test-DVD-$arch&repository=images" | grep 'statuscount code="disabled"' || true)
                    if [ -n "$testdvd_disabled" ]; then
                        echo "Skips openSUSE:$target:Staging:$l due to the testdvd build is disabled"
                        continue
                    fi
                fi

                if [[ $prj =~ ^openSUSE.+:[A-Z]$ ]] || [[ $prj =~ ^openSUSE.+:Gcc[0-9]$ ]]; then
                    echo "Checking $target:$l-$arch"

                    meta=$(mktemp)
                    use_bc="staging_$target:$l-bc-$arch"
                    osc meta prj $prj > $meta
                    if grep -q 0-Bootstrap $meta ; then
                        use_bc=
                    fi
                    if [ -n "$use_bc" ]; then
                        sync_prj openSUSE:$target:Staging:$l/bootstrap_copy "staging_$target:$l-bc-$arch" $arch
                    fi
                    sync_prj openSUSE:$target:Staging:$l/standard staging_$target:$l-$arch $arch
                    regenerate_pl "openSUSE:$target:Staging:$l" $target 1 $use_bc staging_$target:$l-$arch $arch
                    rm $meta
                fi

                if [[ $prj =~ :DVD ]]; then
                    echo "Rebuildpacs $prj"
                    if [ "$dryrun" != 'yes' ]; then
                        perl $SCRIPTDIR/rebuildpacs.pl $prj standard $arch
                    fi
                fi

                if [[ $prj =~ ^openSUSE.+:[A-Z]:DVD$ ]]; then
                    echo "Checking $target:$l:DVD-$arch"
                    sync_prj openSUSE:$target:Staging:$l:DVD/standard "staging_$target:$l-dvd-$arch" $arch
                    regenerate_pl "openSUSE:$target:Staging:$l:DVD" $target 2 $use_bc staging_$target:$l-$arch "staging_$target:$l-dvd-$arch" $arch
                fi
            done
        fi
    done
}

# call main function
start_creating $targets $arch

