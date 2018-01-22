# The package list generator

## Sources

The package list generator is contained within `pkglistgen.py` and consists of
two phases.

- `update`: updates local package cache
- `solve`: uses libsolv to determine list of packages

The two phases can be run together via the `update_and_solve` subcommand which
also performs necessary initialization and post-processing. By default it will
run against all _scopes_, but a specific scope such as `rings` or `staging` can
be specified.

## Input and Output

There is one input package and two output packages:

- 000package-groups: This is the input. It contains the *.product files, as
  well as release package templates (e.g openSUSE-release.spec.in). The special
  files groups.yml is read by pkglistgen.py to output group files that are
  meant to be included by the product files.
- 000product: This is the output container where resulting kiwi files are put
- 000release-packages: This is the output container for release package spec files.

## The groups.yml file

The groups.yml is the input for the solver. It contains dictionaries with lists
of packages. The special key OUTPUT contains a dictionary that lists the group
files to generate.

FIXME: continue here
