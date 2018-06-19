# Metrics Access

Ingest [download.o.o](http://download.opensuse.org/) Apache access logs and generate metrics.

The basic flow:
- stream log file
- decompress
- run through `ingest.php` which creates summary _json_ files

This process runs multiple log ingests concurrently and will wait for all sub-processes to complete.

The cached data is then:
- loaded
- aggregated by intervals (day, week, month)
- summarized
- written to influxdb

A separate, minimal set of aggregation done for each IP protocol data.

## Usage

- `aggregate.php`: invoke to manage entire process including calling (`ingest.php`)
- `ingest.php`: used to parse a single log file / stream and dump summary JSON to stdout

See `~/.cache/openSUSE-release-tools-access` for cache data separated by IP protocol. A single JSON file corresponds to a single access log file.

## Future product versions

All `openSUSE` style product versions are parsed via `ingest.php` and included in summary JSON files. Any request path to either the main product repositories or any respository seemingly built against a product is included. There are many bogus products found on OBS like `openSUSE_Leap_42.22222` and such which are filtered out during the aggregation step. This allows for the products included in the final output to be independent of the parse-time determination. By filtering valid products last, new product patterns may be added after access to those products has begun and been parsed.

- See `REGEX_PRODUCT` in `ingest.php` for the generalized product path detection.
- See `PRODUCT_PATTERN` in `aggregate.php` for the final product filter (note only version number is included).

A possible improvement would be to automate the update of this pattern based on information in OBS.

A product specific annotation may be added to the Grafana dashboard by duplicating the query used for the other products assuming a schedule was added to the `metrics/annotation` directory.

## Factory vs Tumbleweed

Since many repositories that build against Tumbleweed are still named `openSUSE_Factory` and the transition between the names was not done automatically it is not fully possible to determine which "product" was the target. As such all `Factory` and `Tumbleweed` names are merged and counted under `Tumbleweed` including main repository access. This could be extended to show some sort of conversion from `Factory` to `Tumbleweed`, but the primary goal was to show total users.

## Considerations

Given the archival log data is located on a different network from the active data the tool must be run from a machine with access to both or in two steps. Once the summary data has been generated access to the original log files is no longer necessary.

Existing tools (like _telegraf_) were evaluated, but found to be far too slow to process the greater than `20TB` of raw access log data. _PHP_ was selected since it runs around an order of magnitude faster than python to simply open log file and run a "startswith" on against each line. Adding additional logic sees the performance gap widen significantly. After all is said and done ~500,000 entries/second is acheived on each core of development laptop. There is no comparison to the less than 1,000 entries/second processed by _telegraf_.

The original run on `tortuga.suse.de`, using 7 cores, took roughly _23 hours_ to process `22TB` of data into `12GB` of summary data. This data takes up less than `6MB` in _influxdb_ once aggregated.
