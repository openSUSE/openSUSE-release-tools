#!/bin/bash
self=$(readlink $(type -p "$0"))
export project=openSUSE:Leap:15.0
export repos=$project/standard
osrt-pkglistgen
