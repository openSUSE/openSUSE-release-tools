#!/bin/bash
main=openSUSE:Leap:15.0
export delete_kiwis="openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi"

export project=$main:Rings:1-MinimalX
export repos=$project/standard,$main:Rings:0-Bootstrap/standard
osrt-pkglistgen

export project=$main:Rings:2-TestDVD
export repos=$main:Rings:2-TestDVD/standard,$repos
export skip_releases=1
osrt-pkglistgen
