[![Build Status](https://github.com/openSUSE/openSUSE-release-tools/workflows/CI/badge.svg?branch=master)](https://github.com/openSUSE/openSUSE-release-tools/actions?query=branch%3Amaster)
[![codecov](https://codecov.io/gh/openSUSE/openSUSE-release-tools/branch/master/graph/badge.svg?token=MqVygxmguE)](https://codecov.io/gh/openSUSE/openSUSE-release-tools)
[![openSUSE Tumbleweed package](https://repology.org/badge/version-for-repo/opensuse_tumbleweed/opensuse-release-tools.svg)](https://repology.org/metapackage/opensuse-release-tools)

# openSUSE-release-tools

This repository contains a set of tools to aid in the process of building, testing and releasing
(open)SUSE based distributions and their corresponding maintenance updates. You can find more
information in the [docs/processes.md](docs/processes.md). The [CONTENTS.md](CONTENTS.md) file
contains a list of the tools that are included in the repository.

![Rethink release tooling presentation overview](docs/res/workflow-overview.svg)

Everything denoted with a cloud is largely in this repository while the rest is the [open-build-service (OBS)](https://github.com/openSUSE/open-build-service).

## Installation

For non-development usage just install the package.

    zypper in openSUSE-release-tools

Many sub-packages are provided which can be found either by searching or [looking on the build service](https://build.opensuse.org/package/binaries/openSUSE:Tools/openSUSE-release-tools/openSUSE_Factory).

    zypper se openSUSE-release-tools osc-plugin

If CI builds are needed add the [appropriate `openSUSE:Tools` repository](https://software.opensuse.org//download.html?project=openSUSE%3ATools&package=openSUSE-release-tools).

## Usage

All tools provide help documentation accessible via `--help`.

For `osc` plugins include the plugin name after `osc` like the following.

    osc staging --help

For other tools execute the tool directly.

    osrt-repo-checker --help

See the [docs](/docs) directory or a specific tool directory for specific tool documentation outside of `--help`. The [wiki](/wiki) also contains some additional documentation.

## Development

    git clone https://github.com/openSUSE/openSUSE-release-tools.git

If working on an `osc` plugin create symlinks for the plugin and `osclib` in either `~/.osc-plugins` or `/usr/lib/osc-plugins`. For example to install the _staging_ plugin do the following.

    mkdir -p ~/.osc-plugins
    ln -sr ./osc-staging.py ./osclib ~/.osc-plugins

It can also be useful to work against a development copy of `osc` either to utilize new features or to debug/fix functionality. To do so one must place the development copy in the path to be loaded and utilize the wrapper script if working on `osc` plugins. One method to accomplish this is shown below.

    # outside of openSUSE-release-tools checkout
    git clone https://github.com/openSUSE/osc.git

    # inside openSUSE-release-tools checkout
    # note the ending /osc which points to the osc directory within the checkout
    ln -s /path/to/osc/osc ./

    # to utilize the wrapper for working on osc plugins from osrt checkout
    $(realpath ./osc)/../osc-wrapper.py --version

Using [Docker Compose](https://docs.docker.com/compose/), a containerized OBS can be started via one command. The default credentials are `Admin` and `opensuse` on [0.0.0.0:3000](http://0.0.0.0:3000). You can change the port by setting the environment variable `OSRT_EXPOSED_OBS_PORT`.

    docker-compose -f dist/ci/docker-compose.yml up api

To make things easier, you can add an alias to refer to this instance (do not forget to adjust the port if you are using a different one):

    cat <<EOF >> ~/.config/osc/oscrc

    [http://0.0.0.0:3000]
    user = Admin
    pass = opensuse
    aliases = local
    EOF

Then you can use the new `local` alias to access this new instance.

    osc -A local api /about

A facsimile of `openSUSE:Factory` in the form of a subset of the related data can be quickly created in a local OBS instance using the `obs_clone` tool.

    ./obs_clone.py --debug --apiurl-target local

Some tests will attempt to run against the local OBS, but not all.

    nosetests

## Running Continuous Integration

This repository includes all the needed files to set up and run the Continuous Integration test suite. The idea is to use Docker Compose to orchestrate a set of containers, including an OBS instance, and run [the tests](tests/) on top of them. Although they automatically run [on GitHub Actions](https://github.com/features/actions) (more on that later), it is easy to run them locally. The following commands must be executed from the root of the repository.


    # Mount the current path at the /code directory on the container
    sed -i -e "s,../..:,$PWD:," dist/ci/docker-compose.yml

    # Run the linter
    docker-compose -f dist/ci/docker-compose.yml run flaker

    # Run the test full suite (it may take some time)...
    docker-compose -f dist/ci/docker-compose.yml run test

    # .. or just run a single test (i.e., the 'tests/util_tests.py')
    docker-compose -f dist/ci/docker-compose.yml run test run_as_tester pytest tests/util_tests.py

    # We are finished. Now you can shut the containers down.
    docker-compose -f dist/ci/docker-compose.yml down

The [docker-compose.yml](dist/ci/docker-compose.yml) mentions two container images that are built in the [openSUSE:Tools:Images](https://build.opensuse.org/project/show/openSUSE:Tools:Images) project:

* `osrt-miniobs-for-ci` is the base of OBS-related services (API, caches, SMTP, and so on).
* `osrt-testenv-tumbleweed` used to run the tests. The code and the tests are mounted in the `/code` directory of this container.

As mentioned before, the main repository uses GitHub Actions to automatically run the tests when a pull request is opened or the code is pushed to the master branch. You can find the details in the
[workflow definition](.github/workflows/ci-test.yml). Note that, in addition to the steps listed before, code coverage data is submitted to [Codecov](https://app.codecov.io/gh/openSUSE/openSUSE-release-tools).

### Debugging Failures in CI

This section lists a few tricks to debug problems in the CI. You will use your local setup so, as a first step, you need to be able to run the tests as described in the previous section.
To see the logs from all the containers, the following command can be executed:

  docker-compose -f dist/ci/docker-compose.yml logs -f --tail=10

You can run commands in any container by using the docker-compose `exec` command. For instance, you can connect to a container through a shell with the following command (in this case, it will connect to the container behind the `api` service):

  docker-compose -f dist/ci/docker-compose.yml exec api sh

Or you could check the API logs by issuing the following command:

  docker-compose -f dist/ci/docker-compose.yml exec api sh -c 'tail -f /srv/www/obs/api/log/*.log'

To debug problems in the test suite or in the code, place a `breakpoint()` call and you will get access to Python's debugger.

You can access your testing OBS instance at `http://0.0.0.0:3000` and log in using "Admin" as username and "opensuse" as password. To prevent the data being removed while you are inspecting the OBS instance, you can put a call to the `breakpoint()` function.

Finally, if you miss anything for debugging, you can use `zypper` to install it.
