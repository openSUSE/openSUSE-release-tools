#!/bin/bash
self=$(readlink $(type -p "$0"))
export project=openSUSE:Leap:15.0:Ports
export repos=$project/ports
export arch=aarch64
export productrepo=ports
osrt-pkglistgen
