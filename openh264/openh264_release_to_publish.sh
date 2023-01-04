#!/bin/bash

# Release snapshot of data from :POST to :PUBLISH.
# This will result into refreshing repodata for openSUSE's openh264 repo
#
# Contact openSUSE Release Team for more information or contacts
# https://en.opensuse.org/openSUSE:Release_team
#
# More details about OpenH264 in openSUSE at https://en.opensuse.org/OpenH264"

SOURCE_PROJ="openSUSE:Factory:openh264:POST"
PROJ="openSUSE:Factory:openh264:PUBLISHED"

echo "This script will release data from $SOURCE_PROJ into $PROJ."
echo "This step is expected to be executed only after"
echo "confirmation that archive with openh264 rpms was published at ciscobinary.openh264.org"
echo
echo "Pres Enter to proceed or ctrl+c to cancel."
read

osc -A https://build.opensuse.org release --no-delay $SOURCE_PROJ
osc -A https://build.opensuse.org prjresults $SOURCE_PROJ

echo
echo "Done"
