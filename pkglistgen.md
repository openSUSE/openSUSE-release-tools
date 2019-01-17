# Package List Generator

pkglistgen.py is a self contained script to generate and update OBS products for openSUSE and SLE. 
It works on the products and its staging projects and ports.

The main input is a package named 000package-groups and it will update the content of other packages
from that. For that it will read [YAML](https://en.wikipedia.org/wiki/YAML) input from e.g. 000package-groups/groups.yml and generate .group files into 000product. The rest of 000package-groups is copied into 000product as well and it runs the OBS product converter service (See [OBS Documentation](https://en.opensuse.org/openSUSE:Build_Service_product_definition) for details)
The generated release spec files are split into 000release-packages to avoid needless rebuilds. 

## Input

The package list generator reads several files. The most important is groups.yml within 000package-groups

### supportstatus.txt
 TODO
 
### groups.yml
The file is a list of package lists and the special hash 'OUTPUT'. OUTPUT contains an entry for every group file that needs to be written out. The group name of it needs to exist as package list as well. OUTPUT also contains flags for the groups.

We currently support:
 * default-support
 Sets the support level in case there is no explicitly entry in [supportstatus.txt](#supportstatus.txt), defaults to 'unsupported'
 * recommends
 If the solver should take recommends into account when solving the package list, defaults to false.
 * includes
 Adds package lists to the group to be solved. Allows to organize different topics into the same group. By default there are no package lists added - the package list with the group name is always there.
 * excludes
 Removes all packages from the __solved__ groups listed. Used to build addons to main products.
 * conflicts
 Sets package groups not to be part of the same product. Influences the [overlap calculation](#overlap-calculation) only.

Be aware that group names must not contain a '-'.

You can also adapt the solving on a package level by putting a hash into the package list. Normally the package name is a string, in case it's a hash the key needs to be the package name and the value is a list of following modifiers: 

 * recommended
 Evaluate also 'Recommends' in package to determine dependencies. Otherwise only 'required' are considered. Used mainly for patterns in SLE. It can not be combined with platforms, For architecture specific recommends, use patterns.
 * suggested
 Evaluate also 'Suggests' in package to determine dependencies. This implies recommended
 * architecture (e.g. x86_64,s390x,ppc64le,aarch64)
 Makes the entry specific to the listed architectures. Will get ignored if used in combination with 'recommended'.
 * locked
 Do not put the package into this group. Used to *force* certain packages into other modules
 * silent
 Use this package for dependency solving of groups "on top", but do not output the package for this group. Mainly to mark the product to use by adding release packages. Use with care, this breaks dependency chains!

Note that you can write yaml lists in 2 ways. You can put the modifier lists as multiple lines starting with -, but it's recommended to put them as [M1,M2] behind the package name. See the difference between pkg4 and pkg5 in the example. 

#### Example:

```
OUTPUT:
  - group1:
    includes:
    - list1
    - list2
  - group2:
    default-support: l3
    recommends: true
    includes:
    - list3
    excludes:
    - group1
    conflicts:
    - group3
  - group3:
    includes:
    - list2
    
group1:
  - pkg1
  
group2:
  - pkg2: [locked]
  - pkg3
  
group3:
  - pkg4: [x86_64]
  
list1:
  - pkg5:
    - x86_64
  
list2:
  - pkg6: [recommended]
``` 

## Overlap calculcation
 TODO 

