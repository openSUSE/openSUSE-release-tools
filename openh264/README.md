# Tools for manual release workflow of openh264 for openSUSE

Please make sure to ask the openh264 [maintainer](https://build.opensuse.org/project/users/multimedia:libs:cisco-openh264) if there isn't any upcoming change before making a publish request.

I do not expect to make a such request more than twice a year.

We use a three-step approach to ensure that we always have a set of related binaries in OBS.

**Details at https://en.opensuse.org/OpenH264**


## Step 1

Make a snapshot of data that is about to be sent or "POSTed" over for manual extraction at https://ciscobinary.openh264.org

<em>Please note that rpms from this project are signed by the official openSUSE key.</em>

    openh264_release_to_post

This can be only done by somebody who has access to the project openSUSE:Factory:openh264.
Typically done by lkocman

## Step 2

Make an archive from the snapshot of data from **Step 1** ([:POST](https://build.opensuse.org/project/show/openSUSE:Factory:openh264:POST) subproject)

    openh264_make_archive

Trused person / maintainer sends the generated .zip archive containing openSUSE signed rpms attached email to a contact person in Cisco.

<em>Contact the openSUSE Release Team for a particular contact on the Cisco side.</em>


## Step 3

This can be only executed after the archive from **Step 2** was successfully extracted at ciscobinary.openh264.org
You'd get typically confirmation from Cisco over email.

You can manually check presence of files. The example location of published rpms can be found [here](https://en.opensuse.org/OpenH264#Which_files_are_currently_hosted_on_the_Cisco_infra).

Following will release content from [:POST](https://build.opensuse.org/project/show/openSUSE:Factory:openh264:POST) to [:PUBLISHED](https://build.opensuse.org/project/show/openSUSE:Factory:openh264:PUBLISHED) and will trigger repodata refresh of openSUSE's openh264 repositories.

    openh264_release_to_publish
