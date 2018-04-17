from dateutil.parser import parse as date_parse
from metrics import timestamp
import requests
from urlparse import urljoin
import yaml

BASEURL = 'http://review.tumbleweed.boombatower.com/data/'

def data_load(name):
    response = requests.get(urljoin(BASEURL, '{}.yaml'.format(name)))
    return yaml.safe_load(response.text)

def data_write(client, measurement, points):
    client.drop_measurement(measurement)
    client.write_points(points, 's')

def ingest_data(client, name):
    data = data_load(name)

    measurement = 'release_{}'.format(name)
    map_func = globals()['map_{}'.format(name)]
    points = []
    for release, details in data.items():
        points.append({
            'measurement': measurement,
            'fields': map_func(details),
            'time': timestamp(date_parse(release)),
        })

    data_write(client, measurement, points)
    print('wrote {} for {}'.format(len(points), name))

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

def ingest(client):
    if client._database != 'openSUSE:Factory':
        print('skipping release ingest for unsupported project')
        return

    for name in ['bug', 'mail', 'score', 'snapshot']:
        ingest_data(client, name)
