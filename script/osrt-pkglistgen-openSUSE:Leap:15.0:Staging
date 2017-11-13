#!/bin/bash
self=$(readlink $(type -p "$0"))
main=openSUSE:Leap:15.0
: ${letter:=A B C D E}

export delete_kiwis="openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi"
for l in $letter; do
	export project=$main:Staging:$l
	echo "checking $project..."
	export repos=$project/standard
	if [ "$l" != A -a "$l" != B ]; then
		repos="$repos,$project/bootstrap_copy"
	fi

	# DVD project first as it depends on the project below, so might look
	# busy if we update the other one first
	project=$project:DVD repos=$project/standard,$repos skip_releases=1 osrt-pkglistgen

	osrt-pkglistgen
done
