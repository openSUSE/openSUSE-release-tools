ToTest-Manager
==============

ToTest-Manager has two stages:

* Releaser: Release a built product from the main project into the test subproject
* Publisher: Publish the tested product in the test subproject

Both stages can run independently, but they communicate using the `ToTestManagerStatus` attribute to avoid races, like releasing a product while it's being published or overwriting a product while it's being tested. The releaser publish disables the test subproject before releasing a product.

Project Configuration
--------
The configuration is stored in the `ToTestManagerConfig` attribute in YAML format.

Available options and their defaults are:

```
base: openSUSE # Defaults to the toplevel project name
test_subproject: ToTest
test_project: <project>:ToTest # Defaults to <project>:<test_subproject>
do_not_release: False # If set, publishing is a noop (and the releaser doesn't publish disable!)
set_snapshot_number: False
snapshot_number_prefix: Snapshot
take_source_from_product: False
arch: x86_64

# openQA settings
openqa_server: None # URL to the openQA server, e.g. "https://openqa.opensuse.org"
openqa_group: None # Name of the job group in openQA, e.g. "openSUSE Tumbleweed"
jobs_num: 42 # Minimum number of openQA jobs before publishing is possible

# Global defaults for products
product_repo: images
product_arch: local
livecd_repo: images
totest_container_repo: containers
totest_images_repo: images # Default: Same as product_repo.

products:
  (see below)
```

Product Configuration
--------

Every ttm managed project has a list of products which are defined by following attributes:

* `package`: The package name, optionally with multibuild flavor, e.g. "opensuse-tumbleweed-image:docker"
* `archs`: List of architectures the product is built for. Default: `[product_arch]`
* `build_prj`: The project the package is built in. Default: main project
* `build_repo`: The repository the package is built in. Default: `product_repo`
* `needs_to_contain_product_version`: If true, the *.report binary needs to contain the product version in its name. Default: false.
* `max_size`: If set, maximum allowed size of the *.iso binary in bytes. Default: None.
* `release_prj`: The project the product is released into. Default: `test_project` resp. defined by `test_subproject`.
* `release_repo`: The repository the product is released into. Default: `product_repo`
* `release_set_version`: If true, the "setrelease" mechanism (see below) is used.
* `publish_using_release`: Defines the publishing method:  
  True: The package is released from release_prj/release_repo according
        to the releasetarget in the prj meta.  
  False: release_prj/release_repo is publish disabled on release and publish enabled on publish.

To allow a simpler configuration for most common product types and backwards compatibility, there are various kinds of pre-defined product types:

* main: Built in the main project in the `product_repo` for the given architectures and released into the `product_repo` in the test subproject. Optionally, it uses the OBS `set_release` option to set the build number of the products on release. On publish, the `product_repo` in the test subproject is publish enabled.
* livecd: Like main, but uses the `:Live` subproject's `livecd_repo` as source.
* ftp: Like main, but does not use `set_release`.
* image: Like main, but released into the `totest_images_repo` in the test subproject. On publishing, the `totest_images_repo` is publish enabled.
* container: Like main, but released into the `totest_container_repo` instead, which is always publish enabled so that they can be fetched from the OBS registry during testing. For publishing, those products are released into the releasetarget of `totest_container_repo`. This is best combined with a `kind="maintenance_release"` as the target project, to keep older builds instead of overwriting them. For long-living projects, `container-cleaner.py` is run which deletes older images.
* containerfile: Like container, but taken from a repo called `containerfile` instead, where container images are built using `Dockerfile` recipes.

The following product definitions are equivalent:

```
totest_images_repo: appliances
products:
  main:
  - foo:dvd
  ftp:
  - foo:ftp
  livecds:
  - livecd-foo:
    - x86_64
  image:
  - foo:kvm:
    - x86_64
  container:
  - foo-container-image:docker:
    - x86_64
  containerfile:
  - some-dockerfile:
    - x86_64
---
products:
  custom:
    # Implicit defaults for each custom product:
    # archs: [local]
    # build_prj: openSUSE:Factory
    # build_repo: images
    # needs_to_contain_product_version: false
    # max_size: None
    # release_prj: openSUSE:Factory:ToTest
    # release_repo: images
    # release_set_version: false
    # publish_using_release: false
    foo:dvd:
      max_size: 4700372992
      release_set_version: true
    foo:ftp:
      needs_to_contain_product_version: true
    livecd-foo:
      archs: [x86_64]
      release_set_version: true
      build_prj: openSUSE:Factory:Live
    foo:kvm:
      archs: [x86_64]
      release_set_version: true
      release_repo: appliances
    foo-container-image:docker:
      archs: [x86_64]
      release_repo: containers
      publish_using_release: true
    some-dockerfile:
      archs: [x86_64]
      build_repo: containerfile
      release_repo: containers
      publish_using_release: true
```

Every product can have multiple architectures defined, those are only used to check for build success before doing a release. If any of the listed architectures failed to build, a release is blocked.

OBS does not allow to release a multibuild container without all of its flavors, so mentioning a multibuild container itself can be used instead of listing all flavors explicitly. In that case, there is no check for build success for the individual flavors, unless they are listed in addition.

There is a check to ensure that every successful build in the `product_repo` maps to a product. If this is not the case, an error is printed and the release is blocked.

set_release
-----------

The `set_release` mechanism can be enabled products, in which case their build number is overwritten by `snapshot_number_prefix` + a number.
If `take_source_from_product` is enabled, that number is taken from the first "main" product, or if there is none, the first "custom" product.
If `take_source_from_product` is disabled, the `000release-packages:(base)-release` package from the main project's `standard` repo and `arch` is looked at.
