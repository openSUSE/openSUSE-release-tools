from urllib.parse import urljoin
from datetime import datetime

import requests
import yaml
from dateutil.parser import parse as date_parse
from influxdb_client.client.write_api import SYNCHRONOUS

from metrics import timestamp

BASEURL = 'http://review.tumbleweed.boombatower.com/data/'


def data_load(name):
    response = requests.get(urljoin(BASEURL, f'{name}.yaml'))
    return yaml.safe_load(response.text)


def data_drop(client, bucketname, measurement):
    start = "1970-01-01T00:00:00Z"
    stop = datetime.utcnow().isoformat() + "Z"
    delete_api = client.delete_api()
    delete_api.delete(start=start, stop=stop, bucket=bucketname,
                      predicate=f'_measurement="{measurement}"')


def data_write(client, bucketname, measurement, points):
    data_drop(client, bucketname, measurement)
    write_api = client.write_api(write_options=SYNCHRONOUS)
    write_api.write(bucket=bucketname, record=points, write_precision='s')


def ingest_data(client, bucketname, name):
    data = data_load(name)

    measurement = f'release_{name}'
    map_func = globals()[f'map_{name}']
    points = []
    for release, details in data.items():
        points.append({
            'measurement': measurement,
            'fields': map_func(details),
            'time': timestamp(date_parse(release)),
        })

    data_write(client, bucketname, measurement, points)
    print(f'wrote {len(points)} for {name}')


def map_bug(bugs):
    return {
        'bug_count': len(bugs),
    }


def map_mail(details):
    return {
        'reference_count': details['reference_count'],
        'thread_count': details['thread_count'],
    }


def map_score(details):
    return details


def map_snapshot(details):
    return {
        'binary_count': details['binary_count'],
        'binary_unique_count': details['binary_unique_count'],
    }


def ingest(client, bucketname):
    if bucketname != 'openSUSE:Factory':
        print('skipping release ingest for unsupported project')
        return

    for name in ['bug', 'mail', 'score', 'snapshot']:
        ingest_data(client, bucketname, name)
