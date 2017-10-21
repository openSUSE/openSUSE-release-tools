# The package list generator

## Sources

The package list generator consists of two scripts:

- pkglistgen.py - python script using libsolv to do the hard work
- pkglistgen.sh - shell script for the dirty work. It calls the product
  converter locally and then splits the output. See existing scripts in script/
  for examples how to call it for Rings, Stagings etc.

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
