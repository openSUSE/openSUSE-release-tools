# Contents

This document describes in detail the contents of this repository. It is still a *work in progress*,
but do not hesitate to report if something is missing.

## Overview

The repository contains a set of tools to aid in the process of building, testing and releasing
(open)SUSE based distributions. The [Tools](#tools) section enumerates and describes all these
tools, including links to documentation, source code and some information about where they are used.

Apart from these tools, the repository includes:

* Some documentation in the [docs](docs) directory.
* A Python module called [osclib](osclib) which includes code that is shared by several tools. They
  are available in the `osclib` package.
* A Docker-based [tests suite](tests). The Docker manifests and the Docker Compose files are
  located in the [dist](dist) directory.
* [GoCD](https://www.gocd.org) configuration files in [gocd](gocd). GoCD is an open source CI/CD
  server that is used to deploy the bots on OBS.
* A set of [Tampermonkey](https://www.tampermonkey.net) scripts (see [userscript](userscript)
  directory) to extend OBS features when using the web interface.
* Several [systemd](systemd) units: the Metrics and OBS Operator tools make use of
  them.

## Tools

Most of these tools are available as packages for several distributions. Check the [spec file in
this repository](dist/package/openSUSE-release-tools.spec) or the [devel
project](https://build.opensuse.org/package/show/openSUSE:Tools/openSUSE-release-tools) for further
information.

For the time being, we have classified them into three different groups: *command line tools*, *OBS
bots* and *osc plugins*. Bear in mind that the information in the following list might be wrong and
incomplete.

### Command Line Tools

 Usually, the executables are renamed as `osrt-NAME` (e.g., `osrt-announcer`).

#### announcer

Generates email diffs summaries to announce product releases.

* Sources: [factory-package-news/announcer.py](factory-package-news/announcer.py)
* Documentation: [factory-package-news/README.asciidoc](factory-package-news/README.asciidoc)
* Package: openSUSE-release-tools-announcer
* Usage: gocd

#### pkglistgen

Generates and updates OBS products for openSUSE and SLE. It generates package lists based on
`000package-groups` and puts them in `000product` (resulting kiwi files) and `000release-packages`
(release package spec files).

* Sources: [pkglistgen.py](pkglistgen.py)
* Documentation: [docs/pkglistgen.md](docs/pkglistgen.md)
* Package: openSUSE-release-tools-pkglistgen
* Usage: gocd

#### container_cleaner

Clean old containers from a given project like
[openSUSE:Containers:Tumbleweed](https://build.opensuse.org/project/show/openSUSE:Containers:Tumbleweed).
Only those containers providing binaries to the latest five versions for each architecture are kept.

* Sources: [container_cleaner.py](container_cleaner.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: gocd

#### metrics

Generates insightful metrics from relevant OBS and annotation data, based on InfluxDB and Grafana.
See <https://metrics.opensuse.org/>.

* Sources: [metrics.py](metrics.py)
* Documentation: [docs/metrics.md](./docs/metrics.md)
* Package: openSUSE-release-tools-metrics
* Usage: ?

#### metrics-access

Ingests `download.opensuse.org` Apache access logs and generates metrics. It is composed of a PHP
script and a set of [systemd units](systemd).

* Sources: [metrics/access/aggregate.php](metrics/access/aggregate.php)
* Documentation: [docs/metrics.md](./docs/metrics.md)
* Package: openSUSE-release-tools-metrics-access
* Usage: ?

#### totest-manager

Releases distribution snapshots to openQA and publishes if the result is positive.

* Sources: [totest-manager.py](totest-manager.py) and [ttm](ttm)
* Documentation: [ttm/README.md](ttm/README.md)
* Package: openSUSE-release-tools
* Usage: gocd

#### unmaintained

Finds unmaintained binaries sourced from SLE.

* Sources: [unmaintained.py](unmaintained.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### sync-rebuild

Syncs openSUSE:Factory and openSUSE:Factory:Rebuild.

* Sources: [sync-rebuild.py](sync-rebuild.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### bugowner

Manages bugowner information

* Sources: [bugowner.py](bugowner.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### bs_mirrorfull

Mirrors repositories from the build service to a local directory.

* Souces: [bs_mirrorfull](bs_mirrorfull)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: Used by other tools like `pkglistgen` or `repocheck`

#### biarchtool

Manages biarch packages

* Source: [biarchtool.py](biarchtool.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### build-fail-reminder

Sends e-mails about packages failing to build for a long time.

* Sources: [build-fail-reminder.py](build-fail-reminder.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: gocd

#### checknewer

Checks if all packages in a repository are newer than all other repositories.

* Sources: [checknewer.py](checknewer.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### deptool

Assists in debugging dependencies

* Sources: [deptool.py](deptool.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### requestfinder

Allows to retrieve requests from OBS with quite elaborated queries.

* Sources: [requestfinder.py](requestfinder.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ?

#### update_crawler (obsoleted by [origin-manager](#origin-manager))

Create SRs for Leap.

* Sources: [update_crawler.py](update_crawler.py) and [script](script).
* Documentation: --
* Package: openSUSE-release-tools-leaper
* Usage: see [leaper](#leaper)

#### create_staging

Scripts and templates to create staging projects.

* Sources: [staging_templates](staging_templates)
* Documentation: --
* Package: --
* Usage: ?

### Bots

#### check_maintenance_incidents

Handles maintenance incident requests

* Sources: [check_maintenance_incidents.py](check_maintenance_incidents.py)
* Documentation: [docs/maintbot.asciidoc](docs/maintbot.asciidoc)
* Package: openSUSE-release-tools-maintenance
* Usage: gocd

#### leaper

Implements Leap-style services for non-Factory projects (whatever that means).

* Sources: [leaper.py](leaper.py)
* Documentation: --
* Package: openSUSE-release-tools-leaper
* Usage: gocd

#### origin-manager

Keeps track of from what project a package originates, submit updates, review requests to detect origin changes, and enforce origin specific policies like adding appropriate reviews

* Sources: [origin-manager.py](origin-manager.py) and [web](web)
* Documentation: [docs/origin-manager.md](docs/origin-manager.md)
* Package: openSUSE-release-tools-origin-manager
* Usage: gocd

#### staging-bot

Assists in management of staging projects.

* Sources: [devel-project.py][devel-project], [staging-report.py](staging-report.py), [suppkg_rebuild.py](suppkg_rebuild.py).
* Documentation: --
* Package: openSUSE-release-tools-staging-bot
* Usage: gocd

#### legal-auto

Makes automatic legal reviews based on the legaldb API

* Sources: [legal-auto.py](legal-auto.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: gocd

#### check_tags_in_requests

Checks that a submit request has corrrect tags specified.

* Sources: [check_tags_in_requests.py](check_tags_in_requests.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: gocd

#### abichecker

Checks ABI compatibility in OBS requests.

* Sources: [abichecker](abichecker)
* Documentation: --
* Package: openSUSE-release-tools-abichecker
* Usage: gocd

#### check_source_in_factory

Checks if the sources of a submission are either in Factory or a request for Factory with the same
sources exist.

* Sources: [check_source_in_factory.py](check_source_in_factory.py)
* Documentation: [docs/factory-source.asciidoc](docs/factory-source.asciidoc)
* Package: openSUSE-release-tools
* Usage: gocd?

#### openqa-maintenance

OpenQA stuff, not sure about the details.

* Sources: [openqa-maintenance.py](openqa-maintenance.py) and [oqamaint](oqamaint)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: gocd?

#### repo-checker

Inspects built RPMs from staging projects.

* Sources: [project-installcheck.py](project-installcheck.py),
  [staging-installcheck.py](staging-installcheck.py),
  [maintenance-installcheck.py](maintenance-installcheck.py),
  [findfileconflicts](findfileconflicts), [write_repo_susetags_file.pl](write_repo_susetags_file.pl)
* Documentation: --
* Package: openSUSE-release-tools-repo-checker
* Usage: gocd?

#### manager_42.py

Maintains `00Meta/lookup.yml`.

* Sources: [manager_42.py](manager_42.py)
* Documentation: --
* Package: openSUSE-release-tools-leaper
* Usage: Obsoleted by origin-manager?

### OSC Plugins

#### osc-check_source.py

Checks for usual mistakes and problems in the source packages submitted by users. Used also as
review bot that assigns reviews (?).

* Sources: [check_source.py](check_source.py) and [check_source.pl](check_source.pl)
* Documentation: [docs/check_source.asciidoc](docs/check_source.asciidoc)
* Package: openSUSE-release-tools-check-source
* Usage: gocd?

#### osc-origin.py

Tools for working with origin information.

* Sources: [osc-origin.py](osc-origin.py)
* Documentation: [docs/origin-manager.md](docs/origin-manager.md)
* Package: openSUSE-release-tools-origin-manager
* Usage: gocd?

#### osc-cycle.py

Helps with OBS build cycles visualization. See the [openSUSE:Factory/standard example](https://build.opensuse.org/project/repository_state/openSUSE:Factory/standard).

* Sources: [osc-cycle.py](osc-cycle.py)
* Documentation: --
* Package: --
* Usage: ???

#### compare_pkglist.py

Compares packages status between two projects.

* Sources: [compare_pkglist.py](compare_pkglist.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### staging

Manages staging projects.

* Sources: [osc-staging.py](osc-staging.py)
* Documentation: [docs/staging.asciidoc](docs/staging.asciidoc) and [docs/testing.asciidoc](docs/testing.asciidoc)
* Package: osc-plugin-staging
* Usage: ???

#### status.py

Checks the status of the staging workflow bots.

* Sources: [status.py](status.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### fcc_submitter.py

Creates SR from FactoryCandidates to openSUSE Leap project for new build succeded packages.

* Sources: [fcc_submitter.py](fcc_submitter.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### issue-diff.py

Compares packages from a project against factory for differences in referenced issues and presents
changes to allow whitelisting before creating Bugzilla entries.


* Sources: [issue-diff.py](issue-diff.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### obs-clone.py

Clones projects and dependencies between OBS instances.

* Sources: [obs_clone.py](obs_clone.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### obs-operator

Performs staging operations as a service instead of requiring the osc staging plugin to be utilized
directly.

* Sources: [obs_operator.py](obs_operator.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### scan_baselibs.py

Verifies 32bit binaries were imported properly towards a project.

* Sources: [scan_baselibs.py](scan_baselibs.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???

#### k8s-secret.py

Applies kubernetes secrets for OSRT tool osc configuration.

* Sources: [k8s-secret.py](k8s-secret.py)
* Documentation: --
* Package: openSUSE-release-tools
* Usage: ???
