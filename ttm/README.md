ToTest-Manager
==============

ToTest-Manager has two stages:

* Releaser: Release a built product from the main project into the test subproject
* Publisher: Publish the tested product in the test subproject

Both stages can run independently, but they communicate using the `ToTestManagerStatus` attribute to avoid races, like releasing a product while it's being published or overwriting a product while it's being tested. The releaser publish disables the test subproject before releasing a product.

Products
--------

There are various kinds of product types:

* main: Built in the main project in the `product_repo` for the given architectures and released into the `product_repo` in the test subproject. Optionally, it uses the OBS `set_release` option to set the build number of the products on release. On publish, the `product_repo` in the test subproject is publish enabled.
* livecd: Like main, but uses the `:Live` subproject's `livecd_repo` as source.
* ftp: Like main, but does not use `set_release`.
* image: Like main, but released into the `totest_images_repo` in the test subproject. On publishing, the `totest_images_repo` is publish enabled.
* container: Like main, but released into the `totest_container_repo` instead, which is always publish enabled so that they can be fetched from the OBS registry during testing. For publishing, those products are released into the releasetarget of `totest_container_repo`. This is best combined with a `kind="maintenance_release"` as the target project, to keep older builds instead of overwriting them. For long-living projects, `container-cleaner.py` is run which deletes older images.
* containerfile: Like container, but taken from a repo called `containerfile` instead, where container images are built using `Dockerfile` recipes.

Every product can have multiple architectures defined, those are only used to check for build success before doing a release. If any of the listed architectures failed to build, a release is blocked.

OBS does not allow to release a multibuild container without all of its flavors, so mentioning a multibuild container itself can be used instead of listing all flavors explicitly. In that case, there is no check for build success for the individual flavors, unless they are listed in addition.

There is a check to ensure that every successful build in the `product_repo` maps to a product. If this is not the case, an error is printed and the release is blocked.

set_release
-----------

The `set_release` mechanism can be enabled for main, livecd and image products, where their build number is overwritten by `snapshot_number_prefix` + a number. If `take_source_from_product` is set, that number is taken from the first main product (`product_repo` + `product_arch`) or if that doesn't exist, the first image product (first arch). Otherwise, the `000release-packages:(base)-release` package from the main project's `standard` repo and `arch` is looked at.

Configuration
-------------

The configuration is stored in the `ToTestManagerConfig` attribute in YAML format.

```
base: openSUSE # Defaults to the toplevel project name
test_subproject: ToTest
do_not_release: False # If set, publishing is a noop (and the releaser doesn't publish disable!)
need_same_build_number: False # See set_release above
set_snapshot_number: False # See set_release above
snapshot_number_prefix: Snapshot # See set_release above
take_source_from_product: False # See set_release above
arch: x86_64 # See set_release above
jobs_num: 42 # Minimum number of openQA jobs before publishing is possible

product_repo: images
product_arch: local # See set_release above
livecd_repo: images
totest_container_repo: containers
totest_images_repo: images # Repo for image_products. If not set, uses product_repo.

products:
  main:
  - foo:dvd
  livecd:
  - livecd-foo:
    - x86_64
  ftp:
  - foo:ftp
  image:
  - foo:kvm:
    - x86_64
  - foo-container-image:lxc:
    - x86_64
  container:
  - foo-container-image:docker:
    - x86_64
  containerfile:
  - some-dockerfile:
    - x86_64
```
