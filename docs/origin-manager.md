# Origin Manager

The primary function of origin manager is, as the name implies, to _manage_ the _origin_ of packages. In other words to keep track of from what project a package originates, submit updates, review requests to detect origin changes, and enforce origin specific policies like adding appropriate reviews.

The primary configuration for all parts of the tool is the `OSRT:OriginConfig` attribute to be placed on the target project. Once the config is created all relevant caches, listeners, and cron jobs will automatically include the project. The only exception is the `origin-manager` user should be added as a project _reviewer_ in order to activate the review portion of the tool. Conversely either removing the config or locking the project will disable all services. Locking the project will preserve the caches used by the web interface, but not update them which allows for archived viewing.

Additionally, there are some other `OSRT:Origin*` attributes used to override the `OSRT:OriginConfig` outside of the target project and standard `OSRT:Config` relevant options.

## Core concepts

The following core concepts are important for understanding the operation of the tool and its configuration.

### Origin

The primary concept within origin manager is an _origin_ which is simply a project from which a package may originate. The origin config will contain a prioritized list of possible origins. In order to determine the origin for a package each origin is searched in priority order for the package and a matching source revision. The first origin containing a match is considered the origin of the package.

An origin can change for a package without the source of the package changing in the target project in one of two ways. Either a devel project can be set on the target package in conjunction with an allowed `<devel>` origin or the package may change in a potential origin project. If the matching sources are accepted into a higher priority origin then the package will be consider as coming from the new origin. This automatic switching properly encapsulates the rather complicated interrelated product workflow.

Alternatively, if the `OSRT:OriginConfig` is changed that may also change a package's origin.

### Workaround

A _workaround_ is a _pseudo origin_ used to track a package that used to match an origin, but had a source change accepted that matched no origin. The change to a workaround requires human review via the `fallback-group`. If the source later shows up in the origin project it will automatically switch back to not being considered a workaround.

## Configuration

As noted above all configuration for the tool is read from OBS attributes.

### OSRT:OriginConfig

Any target project that expects origin manager to manage it must contain this config. The config is expected to be valid _yaml_ of the following format.

```yaml
origins:
- origin1:
    policy_option1: policy_option1_value

global_option1: global_option1_value
```

Note the origins are a list of single key-value pairs. The list allows the order to be maintained by _yaml_ parsers over a simple key-value set, but makes for a bit more cumbersome markup. The following demonstrates the default config.

```yaml
# Wait instead of declining an initial source submission that cannot be found in
# any origin.
unknown_origin_wait: false

# List of prioritized origins and their policy overrides.
origins: []

# User that will perform reviews (ie. user under which ./origin-manager.py will
# run). If not specified nor available from default reference 'origin-manager'
# will be used.
review-user: <config:origin-manager-review-user>

# Group from which a review will be requested as a fallback whenever automated
# approval is not possible.
fallback-group: <config:origin-manager-fallback-group>

# Fallback workaround project primarily used for migration to origin-manager.
fallback-workaround: {}
```

The following is the default policy for an origin. The policy of the new origin is applied to requests and when determining the current origin (especially relevant to pending* options).

```yaml
# Additional by_group reviews to be added to requests from this origin. Special
# keys supported are: fallback and maintainer. The fallback key will be replaced
# by the fallback-group global configuration value and the maintainer key will
# be replaced by the maintainer from the origin package (by_package review) or
# the devel project for the origin package if configured. Maintainer reviews
# are skipped if the request creator is a maintainer.
additional_reviews: []

# Submit updates for packages from this origin and allow forward (based on
# revision order) submissions without fallback review. If disabled updates will
# not be submitted automatically and manually submitted updates will require
# fallback review.
automatic_updates: true

# Submit new packages from this origin (highest priority, enabled, origin wins).
# New packages will only be submitted once. Any prior requests will block future
# submission of the package until it is accepted.
automatic_updates_initial: false

# Submit updates even if an existing request is still pending.
automatic_updates_supersede: true

# Delay update submission by a minimum of N seconds after a source change.
automatic_updates_delay: 0

# Cap updates at a minimum of N seconds since the last request.
automatic_updates_frequency: 0

# Always add a review for the package maintainer.
maintainer_review_always: false

# Add a review for the package maintainer on the initial submission of package.
maintainer_review_initial: true

# Approve source submissions that match pending submissions in the origin
# project of either the new state or with only allowed reviews
# (pending_submission_allowed_reviews). Otherwise, consider as originating from
# the project containing the pending request and wait for the conditions to be
# met. Disable to require submissions to be accepted before accepting review.
pending_submission_allow: false

# Consider pending submissions for the purposes of determining origin, but wait
# for requests to be accepted before accepting review.
pending_submission_consider: false

# List of reviews to allow for the purposes of accepting review when
# pending_submission_allow is enabled.
pending_submission_allowed_reviews:
# Non-maintenance projects:
- <config_source:staging>*
# Maintenance projects:
- <config_source:review-install-check>
- <config_source:review-openqa>

# Control how early pending requests in an origin project are considered for
# submission to the target project. Submit pending requests with a set of
# allowed reviews, but still wait for the above reviews before being accepted.
# For example !<config:review-team> could be added to require the review team
# approval before mirroring a pending request.
'pending_submission_allowed_reviews_update':
- '!maintenance_incident'
```

The following _special_ origins are supported where `*` denotes all and `~` indicates a workaround (explained later).

- `*`: applied to all origins above it
- `*~`: applied to all workaround origins above it and generates them if they do not exist
- `prefix*suffix`: describes a product family which is expanded into specific projects
- `origin1~`: describes a specific origin workaround
- `<devel>`: replaced with the devel project for a given package if set

To add an origin with a completely default policy use `{}` as shown below.

```yaml
origins:
- projectFoo: {}
```

#### Config references

As one may have noticed, both the global options and policy defaults contain config references denoted by `<config:*>` or `<config_source:*>`. References are replaced with the corresponding value from the `OSRT:Config` for the relevant project.

- `config`: read from the target project
- `config_source`: read from the source project (relative to a source change request)

If no value is found in the relevant config the value is replaced by a blank which in the case of lists removes the entry.

A reference may be used in conjunction with additional characters. This is useful for the `pending_submission_allowed_reviews` policy option which defaults to including `<config_source:staging>*`. This will allow any review that starts with `<config_source:staging>`. For example, when evaluated in the context of `openSUSE:Leap:15.2` for a request from `openSUSE:Factory` this will be evaluated as `openSUSE:Factory:Staging*` which will allow staging reviews.

The reference abstraction is especially useful for cross product family origins. For example _SLE_ can use `openSUSE.org:openSUSE:Factory` as an origin and automatically have the appropriate staging reviews ignored based on the cross-OBS-instance config.

#### Workarounds

When a package is considered a workaround for an origin a `~` will be appended to the origin. Workarounds, just like any origin are only allowed if they are found in the list of origins from the config. Workarounds may be configured with different settings, such as `additional_reviews`, than the non-workaround origin.

Workarounds of any origin may be allowed by adding `*~` origin at the end of the list.

#### Family expansion

Instead of defining specific origin projects for an entire family the config supports family expansion. Note that this has become less useful since layered projects such as maintenance projects have their history stacked on parents for origin finding purposes. As such it is typically sufficient to include just the previous product's `:Update` project instead of the whole family.

Looking at the config from `openSUSE:Leap:15.1` helps explain this feature best. The config contained the following family expansions.

- `SUSE:SLE-15*`
- `openSUSE:Leap:15*`

These were expanded into the following.

- SUSE:SLE-15-SP1:Update
- SUSE:SLE-15-SP1:GA
- SUSE:SLE-15:Update
- SUSE:SLE-15:GA
- openSUSE:Leap:15.0:Update
- openSUSE:Leap:15.0

Note that the _Leap_ expansion automatically did not include the target project nor its `:Update` project as they are considered above it. The `*~` origin in the config meant that each of these was also duplicated for a workaround origin.

Family expansion can also be used with a suffix like what was done in `openSUSE:Leap:15.1:NonFree`.

- openSUSE:Leap:15*:NonFree

The above expanded into the following.

- openSUSE:Leap:15.0:NonFree:Update
- openSUSE:Leap:15.0:NonFree

#### Fallback workaround

The `fallback-workaround` global option is used as a fallback project which will be considered a workaround for another origin. This was designed for the migration to origin manager before which a `:SLE-workarounds` subproject was used to house workaround sources.

The option requires two keys to be set: `project` and `origin`. The `project` key is the project to search for matching sources and the `origin` is what origin of that will be used if matched. For example, `openSUSE:Leap:15.1` used the following.

```yaml
fallback-workaround:
  origin: 'SUSE:SLE-15-SP1:GA~'
  project: 'openSUSE:Leap:15.1:SLE-workarounds'
```

The above config meant that if sources matched no origin, but matched those in `openSUSE:Leap:15.1:SLE-workarounds` the origin was considered `SUSE:SLE-15-SP1:GA~`.

### Override attributes

The following attributes are provided to override their corresponding policy option. These attributes are searched for in the following order of precedence.

- origin package
- origin project
- target package
- target project

If no attributes are found the `OSRT:OriginConfig` policy values for the matched origin are used.

- `OSRT:OriginUpdateSkip`: `automatic_updates = false`
- `OSRT:OriginUpdateSupersede`: `automatic_updates_supersede`
- `OSRT:OriginUpdateDelay`: `automatic_updates_delay`
- `OSRT:OriginUpdateFrequency`: `automatic_updates_frequency`

The `OSRT:OriginUpdateSkip` attribute can also be added to a target project to disable updates entirely, otherwise if the `OSRT:OriginConfig` includes at least one origin with `automatic_updates` then the project will be included in the update jobs.

### Initial submission blacklist

For target projects containing origins with `automatic_updates_initial` enabled the `OSRT:OriginUpdateInitialBlacklist` attribute can be utilized to blacklist new packages from being submitted. The blacklist does not stop those packages from being submitted manually or updated if they are included in the target project. The blacklist only limits the new packages considered for initial submission.

The blacklist supports one entry per line where entries can be a python compatible regular expression and is placed on the target project to be applied to all origins. For example, the following config would be relevant for _Leap_ which has `automatic_updates_initial` enabled for _SLE_, but does not want _SLE_ specific packages.

```
.*SLE.*
.*SLED.*
.*SLES.*
.*suse-manager.*
.*spacewalk.*
kernel-livepatch-.*
system-role-.*
sca-.*
patterns-server-enterprise
unified-installer-release
```

### OSRT:Config

A few options relevant to origin manager are standard options and thus available via the `OSRT:Config` attribute.

```ini
# Group whose members are allowed to issue override commands. The staging-group
# is also included regardless of what value is set.
originmanager-override-group = ''

# Minimum time in seconds to wait before reviewing source submissions. The
# default is 30 minutes with the intention of allowing parrallel submissions
# to origin projects and downstream projects that allow pending requests.
# Without the wait downstream projects can be reviewed before the upstream
# requests have been created. 30 minutes is also less than the 50 minutes that
# staging-bot waits which insure the quick strategy can still be utilized.
originmanager-request-age-min = 1800
```

## Command line interface

A CLI is provided for origin manager via the `osc-plugin-origin` package. See `osc origin --help` for complete reference. A few especially useful examples are included below.

### config --origins-only

The `config` command shows the expanded configuration for a given project. The `--origins-only` flag is useful for ensuring origin expansions are working as expected, but the full output is useful for ensuring policy overrides are working.

```
$ osc origin -p openSUSE:Leap:15.2 config --origins-only
```

```
<devel>
<devel>~
SUSE:SLE-15-SP2:GA
SUSE:SLE-15-SP2:GA~
openSUSE:Leap:15.1:Update
openSUSE:Leap:15.1:Update~
openSUSE:Leap:15.1
openSUSE:Leap:15.1~
openSUSE:Factory
openSUSE:Factory~
```

### package --debug

It can be helpful to understand the origin search path which can be done via the `--debug` flag on the `package` command.

```
$ osc origin -p openSUSE:Leap:15.2 package --debug adminer
```

```
[D] origin_find: openSUSE:Leap:15.2/adminer with source 3bd0d78 (True, True, True)
[D] source_contain: openSUSE:Leap:15.1:Update                16c519a == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1:Update                440c7f2 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1:Update                440c7f2 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1:Update                4c26333 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1:Update                eb15f78 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1                       16c519a == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1                       440c7f2 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1                       440c7f2 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1                       4c26333 == 3bd0d78
[D] source_contain: openSUSE:Leap:15.1                       eb15f78 == 3bd0d78
[D] source_contain: openSUSE:Factory                         3bd0d78 == 3bd0d78 (match)
openSUSE:Factory
```

Which shows that the latest revision from `openSUSE:Factory` is the first match and thus the origin.

### potentials

Understanding the _potential_ origins of a package is also useful.

```
$ osc origin -p openSUSE:Leap:15.2 potentials kernel-source
```

```
origin                                              version
Kernel:openSUSE-15.2                                5.3.8
openSUSE:Leap:15.1:Update                           unknown
openSUSE:Leap:15.1                                  4.12.14
openSUSE:Factory                                    5.3.8
```

### history

Knowning the origin history of a package can also be enlightening.

```
$ osc origin -p openSUSE:Leap:15.2 history kernel-source
```

```
origin                                              state       request
Kernel:openSUSE-15.2                                declined     745644
Kernel:openSUSE-15.2                                review       745323
Kernel:openSUSE-15.2                                accepted     744385
Kernel:openSUSE-15.2                                declined     743969
Kernel:openSUSE-15.2                                declined     743419
Kernel:openSUSE-15.2                                declined     743414
Kernel:openSUSE-15.2                                accepted     742724
Kernel:openSUSE-15.2                                accepted     737241
Kernel:openSUSE-15.2                                accepted     733609
Kernel:openSUSE-15.2                                superseded   733094
Kernel:openSUSE-15.2                                superseded   733005
Kernel:openSUSE-15.2                                accepted     732312
Kernel:openSUSE-15.2                                accepted     731459
openSUSE:Leap:15.1:Update~                          accepted     728396
openSUSE:Leap:15.1:Update~                          accepted     725631
openSUSE:Leap:15.1:Update                           accepted     724147
openSUSE:Leap:15.1:Update                           accepted     721159
```

## Review bot

As with all `ReviewBot` based bots they can be debugged locally by using the `--debug` and `--dry` options. Origin manager provides detailed debug output for understanding both the origin search and policy evaluation. The following example demonstrates a request that is changing the origin from `openSUSE:Leap:15.1:Update` to `openSUSE:Factory`.

```
$ ./origin-manager.py --dry --debug id 746065
```

```
[I] checking 746065
[D] origin_find: openSUSE:Leap:15.2/virtualbox with source d79bf01 (False, True, True)
[D] source_contain: openSUSE:Leap:15.1:Update                1655653 == d79bf01
[D] source_contain: openSUSE:Leap:15.1:Update                f642fe0 == d79bf01
[D] source_contain: openSUSE:Leap:15.1:Update                22bd811 == d79bf01
[D] source_contain: openSUSE:Leap:15.1:Update                48ab129 == d79bf01
[D] source_contain: openSUSE:Leap:15.1:Update                dd5c97b == d79bf01
[D] source_contain: openSUSE:Leap:15.1                       48ab129 == d79bf01
[D] source_contain: openSUSE:Leap:15.1                       dd5c97b == d79bf01
[D] source_contain: openSUSE:Leap:15.1                       da823e6 == d79bf01
[D] source_contain: openSUSE:Leap:15.1                       28ae8a7 == d79bf01
[D] source_contain: openSUSE:Leap:15.1                       0bc96d1 == d79bf01
[D] source_contain: openSUSE:Factory                         d79bf01 == d79bf01 (match)
[D] origin_find: openSUSE:Leap:15.2/virtualbox with source 1655653 (True, True, True)
[D] source_contain: openSUSE:Leap:15.1:Update                1655653 == 1655653 (match)
[D] policy_evaluate:

# policy
additional_reviews: []
automatic_updates: true
automatic_updates_delay: 0
automatic_updates_frequency: 0
automatic_updates_initial: false
automatic_updates_supersede: true
maintainer_review_always: false
maintainer_review_initial: true
pending_submission_allow: false
pending_submission_allowed_reviews:
- openSUSE:Factory:Staging*
pending_submission_allowed_reviews_update:
- '!maintenance_incident'
pending_submission_consider: true

# inputs
direction: unknown
higher_priority: false
new_package: false
origin_change: true
pending_submission: 'False'
same_family: false

PolicyResult(wait=False, accept=True, reviews={'fallback': 'Changing to a lower priority origin.'}, comments=[])
[D] skipped adding duplicate review for origin-reviewers
[D] broadening search to include any state on 746065
[D] no previous comment to replace on 746065
[I] 746065 accepted: origin: openSUSE:Factory
origin_old: openSUSE:Leap:15.1:Update

[D] 746065 review not changed
```

From the `PolicyResult` it is clear that the review will be accepted with the addition of a review for the fallback group since `Changing to a lower priority origin.`.

### Commands

There are a two commands that can be used to control the review bot from an OBS request. The commands follow the standard reply syntax `@<user> <message>` and can be placed in the request description or a comment on the request. Commands issued by users not part of either the `staging-group` or `originmanager-override-group` will be ignored. In order to be accepted commands must match the following regular expression.

```regex
^@(?P<user>[^ ,:]+)[,:]? (?P<args>.*)$
```

The standard `override` command is available to force acceptance when the submission does not match any allowed origin, but this should be avoided if possible. The `unknown_origin_wait` global option can be enabled to make it easier to utilize the override feature.

The origin manager specific command `change_devel` is also available with an expanded user pool to include the request creator. The command tells origin manager to consider the origin as a devel project from the request source project. Alternatively, a different origin project can be optionally specified.

For example, a request originating from `projectDevel` would consider the origin for review purposes as if the devel on the package was set to `projectDevel` using the following command.

```
@origin-manager change_devel
```

The same request would consider the origin for review purposes as if the devel on the package was set to `projectFoo` using the following command.

```
@origin-manager change_devel projectFoo
```

## Automatic updates

For projects with at least one origin having `automatic_updates` enabled source changes from enabled origins will be automatically submitted to the target project. There is both a cron job that runs once daily and an event listener that will make submissions immediately. The listener considers the same control options as the cron job so if something like `automatic_updates_delay` is configured the listener will not make updates for that project and instead they will fallback to the cron job. The cron job also handles configuration changes and backfilling newly managed projects.

### Automatic change_devel

If a source submission is accepted, but during update an origin cannot be matched the annotation on the most recent request will be checked to see if it was considered as devel project. If such an annotation exists a `change_devel` request will automatically be created to match the annotation. After the request is accept future updates will be submitted as appropriate.

## Web interface

A _web interface_ is provided using the _OBS operator_ server which wraps the _origin CLI plugin_. The web interface is backed by a cache which is updated trice-weekly and allows for quickly retrieving the full list of origins for all packages within a project.

In conjunction to the primary web interface a [userscript](../userscript/README.md) is provided to automatically display origin information on the OBS web interface. Both the package view and request views are supplemented and provide links to the web interface. **Note that one must have an active OBS session for either to work.**

### Layout

The interface condenses a rather large amount of information into one screen and can be overwhelming at first glance. The following is a description of the columns from left the right.

#### Package list

A list of all _source_ packages within a target project are shown with their respective origin. The _revisions_ column denotes the type of the last 10 commits: green matches origin in target, red only in origin, and gray for target revisions that do not match origin. The _request_ column shows the highest open request for that package in the target project.

#### Potentials and history

The middle column shows the potential origins for the selected package along with the version within that origin. The two icons are external diff (via OBS) and submit to target from origin.

The origin history shows source change requests for the selected package against the target project. If an annotation is present the origin from the annotation is extracted otherwise the source project is shown.

#### Diff

The last column shows a diff between the selected potential origin and the target project. When a request from the history is also selected it shows the diff between the potential origin and the request source.

### Filtering and sorting

One of the most useful features of the web interface is _filtering and sorting_ which allows for a number different package states to be identified.

The _origin_ column can be sorted to see packages grouped by origin or filtered to show workarounds by entering `~`. Additionally, specific origins can be filtered or even the `None` origin.

The _revisions_ column can be sorted to show the most _behind_ (most red revisions) packages first with the most _worked around_ (most gray revisions) packages next. This is useful for seeing if updates are behind and determining the reason (usually due to bot or human decline).

The _request_ column can be sorted to place packages with requests at the top of the list which is useful when doing lots of reviews against open requests and thus flipping between requests and the origin manager web interface.
