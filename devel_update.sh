#!/usr/bin/bash
#
# Updates devel_packages in https://src.opensuse.org/openSUSE/Factory/src/branch/main/pkgs/_meta/devel_packages
#
#  syntax:
#      get <pkg>
#      set <prj> <pkg>
#      rm  <prj> <pkg>
#      sync
#
#  DEVEL_PACKAGES env should point to the devel_packages clone from
#  repo above, otherwise will look in CWD
#

set +e

function getdevel {
    local pkg="$1"
    awk "{ if ( \$1 == \"$pkg\" ) print \$2 }" "$DEVEL_PACKAGES"
}

function setdevel {
    local prj="$1"
    local pkg="$2"
    if [ x"$prj" == "x" ] || [ x"$pkg" == "x" ]; then
        echo "devel_update set <prj> <pkg>"
        exit 10
    fi

    cat <(awk "{ if ( \$1 != \"$pkg\" ) print }" "$DEVEL_PACKAGES") <(echo $pkg $prj) | sort -d > "$DEVEL_PACKAGES".$$
    mv "$DEVEL_PACKAGES".$$ "$DEVEL_PACKAGES"
}

function rmdevel {
    local prj="$1"
    local pkg="$2"
    if [ x"$prj" == "x" ] || [ x"$pkg" == "x" ]; then
        echo "devel_update rm <prj> <pkg>"
        exit 10
    fi

    awk "{ if ( ! ( \$1 == \"$pkg\" && \$2 == \"$prj\" ) ) print }" "$DEVEL_PACKAGES" > "$DEVEL_PACKAGES".$$
    mv "$DEVEL_PACKAGES".$$ "$DEVEL_PACKAGES"
}

if [ -z "$DEVEL_PACKAGES" ]; then
    DEVEL_PACKAGES=./devel_packages
fi

if ! [ -w "$DEVEL_PACKAGES" ] || ! [ -e "$DEVEL_PACKAGES" ] ; then
    echo "The DEVEL_PACKAGES ($DEVEL_PACKAGES) file is not writable or doesn't exist"
    exit 0
fi

case "$1" in
    get)
        shift
        getdevel "$@"
        ;;
    set)
        shift
        setdevel "$@"
        ;;
    rm)
        shift
        rmdevel "$@"
        ;;
    sync)
        warning=0
        badpkgs=""
        pkgs=$(osc ls openSUSE:Factory)

        # add new packages
        for pkg in $pkgs; do
            if [ "${pkg/*:*/IGNORE}" == "IGNORE" ]; then
                continue
            fi

            grep -q "^$pkg\( \|\$\)" "$DEVEL_PACKAGES"
            if [ $? -ne 0 ]; then
                echo -n "$pkg -> "
                devel=$(osc develproject openSUSE:Factory $pkg 2> /dev/null)
                devel=${devel/\/*/}
                if [ -z "$devel" ]; then
                    devel=$(osc rq list -s accepted -P openSUSE:Factory -p $pkg -t submit | grep "^\s*submit:.* ->\s\+openSUSE:Factory\$" | sed -e "s,^\s*submit:\s*\([^/]\+\)/${pkg}@.*,\1," | uniq)
                    c=$(echo "$devel" | grep -c .)
                    if [ $c -ne 1 ]; then
                        badpkgs="$badpkgs $pkg"
                        warning=1
                        devel="***** UNKNOWN"
                    fi
                fi

                setdevel "$devel" "$pkg"
                echo "$devel"
            fi
        done

        # remove deleted packages
        for pkg in $(awk '{ print $1 }' < "$DEVEL_PACKAGES"); do
            if [[ " $pkgs " != *[[:space:]]"$pkg"[[:space:]]* ]]; then
                echo "removing $pkg"
                d=$(getdevel "$pkg")
                if [ -n "$d" ]; then
                    rmdevel "$d" "$pkg"
                fi
            fi
        done

        # set devel change in last 10 days
        osc rq list -t change_devel -D 10 -P openSUSE:Factory -s accepted |
            grep 'change_devel:\s\+openSUSE:Factory/' |
            sed -e 's,^\s*change_devel:\s*openSUSE:Factory/\([a-zA-Z0-9_+-.]\+\)\s*developed in \([a-zA-Z0-9_+:-]\+\)/\1\s*$,\2 \1,' |
            while read line; do
                setdevel ${line/ */} ${line/* /};
            done

        if [ $warning -ne 0 ]; then
            echo " **** WARNING ****" > /dev/stderr
            echo "Could not fix some packages. Manual intervention required:$badpkgs" > /dev/stderr
        fi

        ;;
    *)
        echo " devel_update (get,set,rm,sync) ...."

esac
