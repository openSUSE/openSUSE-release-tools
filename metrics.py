#!/usr/bin/python

import argparse
from collections import namedtuple
from datetime import datetime
from dateutil.parser import parse as date_parse
from influxdb import InfluxDBClient
from lxml import etree as ET
import os
import subprocess
import sys
import yaml

import metrics_release
import osc.conf
import osc.core
from osc.core import HTTPError
from osc.core import get_commitlog
import osclib.conf
from osclib.cache import Cache
from osclib.conf import Config
from osclib.core import project_pseudometa_package
from osclib.stagingapi import StagingAPI

SOURCE_DIR = os.path.dirname(os.path.realpath(__file__))
Point = namedtuple('Point', ['measurement', 'tags', 'fields', 'time', 'delta'])

# Duplicate Leap config to handle 13.2 without issue.
osclib.conf.DEFAULT[
    r'openSUSE:(?P<project>[\d.]+)'] = osclib.conf.DEFAULT[
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+))']

# Provide osc.core.get_request_list() that swaps out search() implementation to
# capture the generated query, paginate over and yield each request to avoid
# loading all requests at the same time. Additionally, use lxml ET to avoid
# having to re-parse to perform complex xpaths.
def get_request_list(*args, **kwargs):
    osc.core._search = osc.core.search
    osc.core.search = search_capture
    osc.core._ET = osc.core.ET
    osc.core.ET = ET

    osc.core.get_request_list(*args, **kwargs)

    osc.core.search = osc.core._search

    query = search_capture.query
    for request in search_paginated_generator(query[0], query[1], **query[2]):
        # Python 3 yield from.
        yield request

    osc.core.ET = osc.core._ET

def search_capture(apiurl, queries=None, **kwargs):
    search_capture.query = (apiurl, queries, kwargs)
    return {'request': ET.fromstring('<collection matches="0"></collection>')}

# Provides a osc.core.search() implementation for use with get_request_list()
# that paginates in sets of 1000 and yields each request.
def search_paginated_generator(apiurl, queries=None, **kwargs):
    if "submit/target/@project='openSUSE:Factory'" in kwargs['request']:
        kwargs['request'] = osc.core.xpath_join(kwargs['request'], '@id>250000', op='and')

    request_count = 0
    queries['request']['limit'] = 1000
    queries['request']['offset'] = 0
    while True:
        collection = osc.core.search(apiurl, queries, **kwargs)['request']
        if not request_count:
            print('processing {:,} requests'.format(int(collection.get('matches'))))

        for request in collection.findall('request'):
            yield request
            request_count += 1

        if request_count == int(collection.get('matches')):
            # Stop paging once the expected number of items has been returned.
            break

        # Release memory as otherwise ET seems to hold onto it.
        collection.clear()
        queries['request']['offset'] += queries['request']['limit']

points = []

def point(measurement, fields, datetime, tags={}, delta=False):
    global points
    points.append(Point(measurement, tags, fields, timestamp(datetime), delta))

def timestamp(datetime):
    return int(datetime.strftime('%s'))

def ingest_requests(api, project):
    requests = get_request_list(api.apiurl, project,
                                req_state=('accepted', 'revoked', 'superseded'),
                                exclude_target_projects=[project],
                                withfullhistory=True)
    for request in requests:
        if request.find('action').get('type') not in ('submit', 'delete'):
            # TODO Handle non-stageable requests via different flow.
            continue

        created_at = date_parse(request.find('history').get('when'))
        final_at = date_parse(request.find('state').get('when'))
        final_at_history = date_parse(request.find('history[last()]').get('when'))
        if final_at_history > final_at:
            # Workaround for invalid dates: openSUSE/open-build-service#3858.
            final_at = final_at_history

        # TODO Track requests in psuedo-ignore state.
        point('total', {'backlog': 1, 'open': 1}, created_at, {'event': 'create'}, True)
        point('total', {'backlog': -1, 'open': -1}, final_at, {'event': 'close'}, True)

        request_tags = {}
        request_fields = {
            'total': (final_at - created_at).total_seconds(),
            'staged_count': len(request.findall('review[@by_group="factory-staging"]/history')),
        }
        # TODO Total time spent in backlog (ie factory-staging, but excluding when staged).

        staged_first_review = request.xpath('review[contains(@by_project, "{}:Staging:")]'.format(project))
        if len(staged_first_review):
            by_project = staged_first_review[0].get('by_project')
            request_tags['type'] = 'adi' if api.is_adi_project(by_project) else 'letter'

            # TODO Determine current whitelists state based on dashboard revisions.
            if project.startswith('openSUSE:Factory'):
                splitter_whitelist = 'B C D E F G H I J'.split()
                if splitter_whitelist:
                    short = api.extract_staging_short(by_project)
                    request_tags['whitelisted'] = short in splitter_whitelist
            else:
                # All letter where whitelisted since no restriction.
                request_tags['whitelisted'] = request_tags['type'] == 'letter'

        ready_to_accept = request.xpath('review[contains(@by_project, "{}:Staging:adi:") and @state="accepted"]/history[comment[text() = "ready to accept"]]/@when'.format(project))
        if len(ready_to_accept):
            ready_to_accept = date_parse(ready_to_accept[0])
            request_fields['ready'] = (final_at - ready_to_accept).total_seconds()

            # TODO Points with indentical timestamps are merged so this can be placed in total
            # measurement, but may make sense to keep this separate and make the others follow.
            point('ready', {'count': 1}, ready_to_accept, delta=True)
            point('ready', {'count': -1}, final_at, delta=True)

        staged_first = request.xpath('review[@by_group="factory-staging"]/history/@when')
        if len(staged_first):
            staged_first = date_parse(staged_first[0])
            request_fields['staged_first'] = (staged_first - created_at).total_seconds()

            # TODO Decide if better to break out all measurements by time most relevant to event,
            # time request was created, or time request was finalized. It may also make sense to
            # keep separate measurement by different times like this one.
            point('request_staged_first', {'value': request_fields['staged_first']}, staged_first, request_tags)

        point('request', request_fields, final_at, request_tags)

        # Staging related reviews.
        for number, review in enumerate(
            request.xpath('review[contains(@by_project, "{}:Staging:")]'.format(project)), start=1):
            staged_at = date_parse(review.get('when'))

            project_type = 'adi' if api.is_adi_project(review.get('by_project')) else 'letter'
            short = api.extract_staging_short(review.get('by_project'))
            point('staging', {'count': 1}, staged_at,
                  {'id': short, 'type': project_type, 'event': 'select'}, True)
            point('total', {'backlog': -1, 'staged': 1}, staged_at, {'event': 'select'}, True)

            who = who_workaround(request, review)
            review_tags = {'event': 'select', 'user': who, 'number': number}
            review_tags.update(request_tags)
            point('user', {'count': 1}, staged_at, review_tags)

            history = review.find('history')
            if history is not None:
                unselected_at = date_parse(history.get('when'))
            else:
                unselected_at = final_at

            # If a request is declined and re-opened it must be repaired before being re-staged. At
            # which point the only possible open review should be the final one.
            point('staging', {'count': -1}, unselected_at,
                  {'id': short, 'type': project_type, 'event': 'unselect'}, True)
            point('total', {'backlog': 1, 'staged': -1}, unselected_at, {'event': 'unselect'}, True)

        # No-staging related reviews.
        for review in request.xpath('review[not(contains(@by_project, "{}:Staging:"))]'.format(project)):
            tags = {
                # who_added is non-trivial due to openSUSE/open-build-service#3898.
                'state': review.get('state'),
            }

            opened_at = date_parse(review.get('when'))
            history = review.find('history')
            if history is not None:
                completed_at = date_parse(history.get('when'))
                tags['who_completed'] = history.get('who')
            else:
                completed_at = final_at
                # Does not seem to make sense to mirror user responsible for making final state
                # change as the user who completed the review.

            tags['key'] = []
            tags['type'] = []
            for name, value in sorted(review.items(), reverse=True):
                if name.startswith('by_'):
                    tags[name] = value
                    tags['key'].append(value)
                    tags['type'].append(name[3:])
            tags['type'] = '_'.join(tags['type'])

            point('review', {'open_for': (completed_at - opened_at).total_seconds()}, completed_at, tags)
            point('review_count', {'count':  1}, opened_at, tags, True)
            point('review_count', {'count': -1}, completed_at, tags, True)

        found = []
        for set_priority in request.xpath('history[description[contains(text(), "Request got a new priority:")]]'):
            parts = set_priority.find('description').text.rsplit(' ', 3)
            priority_previous = parts[1]
            priority = parts[3]
            if priority == priority_previous:
                continue

            changed_at = date_parse(set_priority.get('when'))
            if priority_previous != 'moderate':
                point('priority', {'count': -1}, changed_at, {'level': priority_previous}, True)
            if priority != 'moderate':
                point('priority', {'count': 1}, changed_at, {'level': priority}, True)
                found.append(priority)

        # Ensure a final removal entry is created when request is finalized.
        priority = request.find('priority')
        if priority is not None and priority.text != 'moderate':
            if priority.text in found:
                point('priority', {'count': -1}, final_at, {'level': priority.text}, True)
            else:
                print('unable to find priority history entry for {} to {}'.format(request.get('id'), priority.text))

    print('finalizing {:,} points'.format(len(points)))
    return walk_points(points, project)

def who_workaround(request, review, relax=False):
    # Super ugly workaround for incorrect and missing data:
    # - openSUSE/open-build-service#3857
    # - openSUSE/open-build-service#3898
    global who_workaround_swap, who_workaround_miss

    who = review.get('who') # All that should be required (used as fallback).
    when = review.get('when')
    if relax:
        # Super hack, chop off seconds to relax in hopes of finding potential.
        when = when[:-2]

    who_real = request.xpath(
        'history[contains(@when, "{}") and comment[contains(text(), "{}")]]/@who'.format(
            when, review.get('by_project')))
    if len(who_real):
        who = who_real[0]
        who_workaround_swap += 1
    elif not relax:
        return who_workaround(request, review, True)
    else:
        who_workaround_miss += 1

    return who

# Walk data points in order by time, adding up deltas and merging points at
# the same time. Data is converted to dict() and written to influx batches to
# avoid extra memory usage required for all data in dict() and avoid influxdb
# allocating memory for entire incoming data set at once.
def walk_points(points, target):
    global client

    measurements = set()
    counters = {}
    final = []
    time_last = None
    wrote = 0
    for point in sorted(points, key=lambda l: l.time):
        if point.measurement not in measurements:
            # Wait until just before writing to drop measurement.
            client.drop_measurement(point.measurement)
            measurements.add(point.measurement)

        if point.time != time_last and len(final) >= 1000:
            # Write final point in batches of ~1000, but guard against writing
            # when in the middle of points at the same time as they may end up
            # being merged. As such the previous time should not match current.
            client.write_points(final, 's')
            wrote += len(final)
            final = []
        time_last = point.time

        if not point.delta:
            final.append(dict(point._asdict()))
            continue

        # A more generic method like 'key' which ended up being needed is likely better.
        measurement = counters_tag_key = point.measurement
        if measurement == 'staging':
            counters_tag_key += point.tags['id']
        elif measurement == 'review_count':
            counters_tag_key += '_'.join(point.tags['key'])
        elif measurement == 'priority':
            counters_tag_key += point.tags['level']
        counters_tag = counters.setdefault(counters_tag_key, {'last': None, 'values': {}})

        values = counters_tag['values']
        for key, value in point.fields.items():
            values[key] = values.setdefault(key, 0) + value

        if counters_tag['last'] and point.time == counters_tag['last']['time']:
            point = counters_tag['last']
        else:
            point = dict(point._asdict())
            counters_tag['last'] = point
            final.append(point)
        point['fields'].update(counters_tag['values'])

    # Write any remaining final points.
    client.write_points(final, 's')
    return wrote + len(final)

def ingest_release_schedule(project):
    points = []
    release_schedule = {}
    release_schedule_file = os.path.join(SOURCE_DIR, 'metrics/annotation/{}.yaml'.format(project))
    if project.endswith('Factory'):
        # TODO Pending resolution to #1250 regarding deployment.
        return 0

        # Extract Factory "release schedule" from Tumbleweed snapshot list.
        command = 'rsync rsync.opensuse.org::opensuse-full/opensuse/tumbleweed/iso/Changes.* | ' \
            'grep -oP "Changes\.\K\d{5,}"'
        snapshots = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE).communicate()[0]
        for date in snapshots.split():
            release_schedule[datetime.strptime(date, '%Y%m%d')] = 'Snapshot {}'.format(date)
    elif os.path.isfile(release_schedule_file):
        # Load release schedule for non-rolling releases from yaml file.
        with open(release_schedule_file, 'r') as stream:
            release_schedule = yaml.safe_load(stream)

    for date, description in release_schedule.items():
        points.append({
            'measurement': 'release_schedule',
            'fields': {'description': description},
            'time': timestamp(date),
        })

    client.drop_measurement('release_schedule')
    client.write_points(points, 's')
    return len(points)

def revision_index(api):
    if not hasattr(revision_index, 'index'):
        revision_index.index = {}

        project, package = project_pseudometa_package(api.apiurl, api.project)
        try:
            root = ET.fromstringlist(
                get_commitlog(api.apiurl, project, package, None, format='xml'))
        except HTTPError as e:
            return revision_index.index

        for logentry in root.findall('logentry'):
            date = date_parse(logentry.find('date').text)
            revision_index.index[date] = logentry.get('revision')

    return revision_index.index

def revision_at(api, datetime):
    index = revision_index(api)
    for made, revision in sorted(index.items(), reverse=True):
        if made <= datetime:
            return revision

    return None

def dashboard_at(api, filename, datetime=None, revision=None):
    if datetime:
        revision = revision_at(api, datetime)
    if not revision:
        return revision

    content = api.pseudometa_file_load(filename, revision)
    if filename in ('ignored_requests'):
        if content:
            return yaml.safe_load(content)
        return {}
    elif filename in ('config'):
        if content:
            # TODO re-use from osclib.conf.
            from ConfigParser import ConfigParser
            import io

            cp = ConfigParser()
            config = '[remote]\n' + content
            cp.readfp(io.BytesIO(config))
            return dict(cp.items('remote'))
        return {}

    return content

def dashboard_at_changed(api, filename, revision=None):
    if not hasattr(dashboard_at_changed, 'previous'):
        dashboard_at_changed.previous = {}

    content = dashboard_at(api, filename, revision=revision)

    if content is None and filename == 'repo_checker' and api.project == 'openSUSE:Factory':
        # Special case to fallback to installcheck pre repo_checker file.
        return dashboard_at_changed(api, 'installcheck', revision)

    if content and content != dashboard_at_changed.previous.get(filename):
        dashboard_at_changed.previous[filename] = content
        return content

    return None

def ingest_dashboard_config(content):
    if not hasattr(ingest_dashboard_config, 'previous'):
        result = client.query('SELECT * FROM dashboard_config ORDER BY time DESC LIMIT 1')
        if result:
            # Extract last point and remove zero values since no need to fill.
            point = next(result.get_points())
            point = {k: v for (k, v) in point.iteritems() if k != 'time' and v != 0}
            ingest_dashboard_config.previous = set(point.keys())
        else:
            ingest_dashboard_config.previous = set()

    fields = {}
    for key, value in content.items():
        if key.startswith('repo_checker-binary-whitelist'):
            ingest_dashboard_config.previous.add(key)

            fields[key] = len(value.split())

    # Ensure any previously seen key are filled with zeros if no longer present
    # to allow graphs to fill with previous.
    fields_keys = set(fields.keys())
    missing = ingest_dashboard_config.previous - fields_keys
    if len(missing):
        ingest_dashboard_config.previous = fields_keys

        for key in missing:
            fields[key] = 0

    return fields

def ingest_dashboard_devel_projects(content):
    return {
        'count': len(content.strip().split()),
    }

def ingest_dashboard_repo_checker(content):
    return {
        'install_count': content.count("can't install "),
        'conflict_count': content.count('found conflict of '),
        'line_count': content.count('\n'),
    }

def ingest_dashboard_version_snapshot(content):
    return {
        'version': content.strip(),
    }

def ingest_dashboard_revision_get():
    result = client.query('SELECT revision FROM dashboard_revision ORDER BY time DESC LIMIT 1')
    if result:
        return next(result.get_points())['revision']

    return None

def ingest_dashboard(api):
    index = revision_index(api)

    revision_last = ingest_dashboard_revision_get()
    past = True if revision_last is None else False
    print('dashboard ingest: processing {:,} revisions starting after {}'.format(
        len(index), 'the beginning' if past else revision_last))

    filenames = ['config', 'repo_checker', 'version_snapshot']
    if api.project == 'openSUSE:Factory':
        filenames.append('devel_projects')

    count = 0
    points = []
    for made, revision in sorted(index.items()):
        if not past:
            if revision == revision_last:
                past = True
            continue

        time = timestamp(made)
        for filename in filenames:
            content = dashboard_at_changed(api, filename, revision)
            if content:
                map_func = globals()['ingest_dashboard_{}'.format(filename)]
                fields = map_func(content)
                if not len(fields):
                    continue

                points.append({
                    'measurement': 'dashboard_{}'.format(filename),
                    'fields': fields,
                    'time': time,
                })

        points.append({
            'measurement': 'dashboard_revision',
            'fields': {
                'revision': revision,
            },
            'time': time,
        })

        if len(points) >= 1000:
            client.write_points(points, 's')
            count += len(points)
            points = []

    if len(points):
        client.write_points(points, 's')
        count += len(points)

    print('last revision processed: {}'.format(revision if len(index) else 'none'))

    return count

def main(args):
    global client
    client = InfluxDBClient(args.host, args.port, args.user, args.password, args.project)

    osc.conf.get_config(override_apiurl=args.apiurl)
    apiurl = osc.conf.config['apiurl']
    osc.conf.config['debug'] = args.debug

    # Ensure database exists.
    client.create_database(client._database)

    metrics_release.ingest(client)
    if args.release_only:
        return

    # Use separate cache since it is persistent.
    _, package = project_pseudometa_package(apiurl, args.project)
    if args.wipe_cache:
        Cache.delete_all()
    if args.heavy_cache:
        Cache.PATTERNS['/search/request'] = sys.maxint
        Cache.PATTERNS['/source/[^/]+/{}/_history'.format(package)] = sys.maxint
    Cache.PATTERNS['/source/[^/]+/{}/[^/]+\?rev=.*'.format(package)] = sys.maxint
    Cache.init('metrics')

    Config(apiurl, args.project)
    api = StagingAPI(apiurl, args.project)

    print('dashboard: wrote {:,} points'.format(ingest_dashboard(api)))

    global who_workaround_swap, who_workaround_miss
    who_workaround_swap = who_workaround_miss = 0

    points_requests = ingest_requests(api, args.project)
    points_schedule = ingest_release_schedule(args.project)

    print('who_workaround_swap', who_workaround_swap)
    print('who_workaround_miss', who_workaround_miss)

    print('wrote {:,} points and {:,} annotation points to db'.format(
        points_requests, points_schedule))


if __name__ == '__main__':
    description = 'Ingest relevant OBS and annotation data to generate insightful metrics.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', help='OBS instance API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print useful debugging info')
    parser.add_argument('-p', '--project', default='openSUSE:Factory', help='OBS project')
    parser.add_argument('--host', default='localhost', help='InfluxDB host')
    parser.add_argument('--port', default=8086, help='InfluxDB post')
    parser.add_argument('--user', default='root', help='InfluxDB user')
    parser.add_argument('--password', default='root', help='InfluxDB password')
    parser.add_argument('--wipe-cache', action='store_true', help='wipe GET request cache before executing')
    parser.add_argument('--heavy-cache', action='store_true',
                        help='cache ephemeral queries indefinitely (useful for development)')
    parser.add_argument('--release-only', action='store_true', help='ingest release metrics only')
    args = parser.parse_args()

    sys.exit(main(args))
