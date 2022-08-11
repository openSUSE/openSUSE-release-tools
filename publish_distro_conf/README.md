# publish_distro configuration files

## Tools

configuration files in this repository are used by `../publish_distro`
to publish content from ftp-stage to ftp-prod on the pontifex host

Individual releases have its own section in 
`pontifex.infra.opensuse.org:~mirror/bin/publish_factory_leap` which
is triggered by a cronjob on a regular basis.


## Getting access to the host

Users need to have [openSUSE Heroes](https://en.opensuse.org/openSUSE:Heroes) VPN access to be able to access pontifex.infra.opensuse.org.

## Deployment of publish_distro and related configuration

publish_distro should be deployed as part of `openSUSE-release-tools` rpm by openSUSE heroes.
Configuration files will be currently checked out from git under the `mirror` user.