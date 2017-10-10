#!/bin/bash
self=$(readlink $(type -p "$0"))
main=openSUSE:Leap:15.0
: ${letter:=A B C D E}

export delete_kiwis="openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi"
for l in $letter; do
	export project=$main:Staging:$l
	export repos=$project/standard
	if [ "$l" != A -a "$l" != B ]; then
		repos="$repos,$project/bootstrap_copy"
	fi
	osrt-pkglistgen

	export project=$project:DVD
	export repos=$project/standard,$repos
	export skip_releases=1
	osrt-pkglistgen
done
