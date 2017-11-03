# Metrics

Ingest relevant OBS and annotation data to generate insightful metrics.

The overall structure is to loop over all requests to extract point of change
and calculate metrics. Many of the points of change are treated as deltas,
meaning add one to this bucket or minus one from another. After all requests are
ingested the points are walked to evaluate the deltas and recreate the state at
that point in time.

OBS provides incomplete and inconsistent data which causes odd results like
negative backlog counts. The ingest tool implements a number of workarounds to
figure out the correct values when possible, but when not possible the provided
values are used rather than excluding a fairly large chunk of data. The main
issues are documented in:

- openSUSE/open-build-service#3857
- openSUSE/open-build-service#3858
- openSUSE/open-build-service#3897
- openSUSE/open-build-service#3898

## Pre-requisites

- InfluxDB instance
- Grafana instance
  - `grafana.ini`:
    - `[dashboards.json].enabled = true` to use the dashboards provided by rpm
  - create data sources for desired projects
    setting name and database to the project name (ex. `openSUSE:Factory`)

## Usage

See help information for InfluxDB connection flags.

```
./metrics -p openSUSE:Factory
```

Once completed the Grafana dashboard should make pretty graphs.

## Development

Grafana provides an export to JSON option which can be used when the dashboards
are modified to export them and version control the changes in git. Ensure not
to unintentionally change the default project, annotation state, or time period
by saving the dashboard with different defaults.

Use the `--debug` option and inspect individual request XML dumps by looking in
`~/.cache/osc-plugin-factory-metrics` or:

```
osc api '/request/$reqid?withfullhistory=1'
```

When adding new delta based metrics it may be necessary to add key logic in
`walk_points()` to handle proper grouping for evaluation of deltas.
