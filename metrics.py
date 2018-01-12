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

import osc.conf
import osc.core
import osclib.conf
from osclib.cache import Cache
from osclib.conf import Config
from osclib.stagingapi import StagingAPI

SOURCE_DIR = os.path.dirname(os.path.realpath(__file__))
Point = namedtuple('Point', ['measurement', 'tags', 'fields', 'time', 'delta'])

# Duplicate Leap config to handle 13.2 without issue.
osclib.conf.DEFAULT[
    r'openSUSE:(?P<project>[\d.]+)'] = osclib.conf.DEFAULT[
    r'openSUSE:(?P<project>Leap:[\d.]+)']

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
    # Wait until just before writing to drop database.
    client.drop_database(client._database)
    client.create_database(client._database)

    counters = {}
    final = []
    time_last = None
    wrote = 0
    for point in sorted(points, key=lambda l: l.time):
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

    client.write_points(points, 's')
    return len(points)

def main(args):
    global client
    client = InfluxDBClient(args.host, args.port, args.user, args.password, args.project)

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config['debug'] = args.debug

    # Use separate cache since it is persistent.
    Cache.CACHE_DIR = os.path.expanduser('~/.cache/osc-plugin-factory-metrics')
    if args.wipe_cache:
        Cache.delete_all()
    Cache.PATTERNS['/search/request'] = sys.maxint
    Cache.init()

    Config(args.project)
    api = StagingAPI(osc.conf.config['apiurl'], args.project)

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
    args = parser.parse_args()

    sys.exit(main(args))
