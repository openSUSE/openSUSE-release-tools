from __future__ import print_function
from datetime import timedelta
import json
import logging
import os
import os.path
from osc import cmdln
from osc import core
from osc import oscerr
from osc.core import get_request_list
from osclib.cache import Cache
from osclib.cache_manager import CacheManager
from osclib.core import package_list_without_links
from osclib.origin import config_load
from osclib.origin import config_origin_list
from osclib.origin import origin_find
from osclib.origin import origin_revision_state
from osclib.util import mail_send
from shutil import copyfile
import sys
import time
import yaml

OSRT_ORIGIN_LOOKUP_TTL = 60 * 60 * 24 * 7

@cmdln.option('--debug', action='store_true', help='output debug information')
@cmdln.option('--diff', action='store_true', help='diff against previous report')
@cmdln.option('--dry', action='store_true', help='perform a dry-run where applicable')
@cmdln.option('--force-refresh', action='store_true', help='force refresh of data')
@cmdln.option('--format', default='plain', help='output format')
@cmdln.option('--mail', action='store_true', help='mail report to <confg:mail-release-list>')
@cmdln.option('--origins-only', action='store_true', help='list origins instead of expanded config')
@cmdln.option('-p', '--project', help='project on which to operate (default is openSUSE:Factory)')
def do_origin(self, subcmd, opts, *args):
    """${cmd_name}: tools for working with origin information

    ${cmd_option_list}

    config: print expanded OSRT:OriginConfig
    list: print all packages and their origin
    package: print the origin of package
    report: print origin summary report

    Usage:
        osc origin config [--origins-only]
        osc origin list [--force-refresh] [--format json|yaml]
        osc origin package [--debug] PACKAGE
        osc origin report [--diff] [--force-refresh] [--mail]
    """

    if len(args) == 0:
        raise oscerr.WrongArgs('A command must be indicated.')
    command = args[0]
    if command not in ['config', 'list', 'package', 'report']:
        raise oscerr.WrongArgs('Unknown command: {}'.format(command))
    if command == 'package' and len(args) < 2:
        raise oscerr.WrongArgs('A package must be indicated.')

    level = logging.DEBUG if opts.debug else None
    logging.basicConfig(level=level, format='[%(levelname).1s] %(message)s')

    # Allow for determining project from osc store.
    if not opts.project:
        if core.is_project_dir('.'):
            opts.project = core.store_read_project('.')
        else:
            opts.project = 'openSUSE:Factory'

    Cache.init()
    apiurl = self.get_api_url()
    config = config_load(apiurl, opts.project)
    if not config:
        raise oscerr.WrongArgs('OSRT:OriginConfig attribute missing from {}'.format(opts.project))

    function = 'osrt_origin_{}'.format(command)
    globals()[function](apiurl, opts, *args[1:])

def osrt_origin_config(apiurl, opts, *args):
    config = config_load(apiurl, opts.project)

    if opts.origins_only:
        print('\n'.join(config_origin_list(config)))
    else:
        yaml.Dumper.ignore_aliases = lambda *args : True
        print(yaml.dump(config))

def osrt_origin_dump(format, data):
    if format == 'json':
        print(json.dumps(data))
    elif format == 'yaml':
        print(yaml.dump(data))
    else:
        if format != 'plain':
            print('unknown format: {}'.format(format), file=sys.stderr)
        return False
    return True

def osrt_origin_lookup_file(project, previous=False):
    parts = [project, 'yaml']
    if previous:
        parts.insert(1, 'previous')
    lookup_name = '.'.join(parts)
    cache_dir = CacheManager.directory('origin-manager')
    return os.path.join(cache_dir, lookup_name)

def osrt_origin_lookup(apiurl, project, force_refresh=False, previous=False, quiet=False):
    lookup_path = osrt_origin_lookup_file(project, previous)
    if not force_refresh and os.path.exists(lookup_path):
        if not previous and time.time() - os.stat(lookup_path).st_mtime > OSRT_ORIGIN_LOOKUP_TTL:
            return osrt_origin_lookup(apiurl, project, True)

        with open(lookup_path, 'r') as lookup_stream:
            lookup = yaml.safe_load(lookup_stream)

            if not isinstance(lookup.itervalues().next(), dict):
                # Convert flat format to dictionary.
                for package, origin in lookup.items():
                    lookup[package] = {'origin': origin}
    else:
        if previous:
            return None

        packages = package_list_without_links(apiurl, project)
        logging.debug('{} packages found in {}'.format(len(packages), project))

        lookup = {}
        for package in packages:
            origin_info = origin_find(apiurl, project, package)
            lookup[str(package)] = {
                'origin': str(origin_info),
                'revisions': origin_revision_state(apiurl, project, package, origin_info),
            }

        if os.path.exists(lookup_path):
            lookup_path_previous = osrt_origin_lookup_file(project, True)
            copyfile(lookup_path, lookup_path_previous)

        with open(lookup_path, 'w+') as lookup_stream:
            yaml.dump(lookup, lookup_stream, default_flow_style=False)

    if not previous and not quiet:
        dt = timedelta(seconds=time.time() - os.stat(lookup_path).st_mtime)
        print('# generated {} ago'.format(dt), file=sys.stderr)

    return lookup

def osrt_origin_max_key(dictionary, minimum):
    return max(len(max(dictionary.keys(), key=len)), minimum)

def osrt_origin_list(apiurl, opts, *args):
    lookup = osrt_origin_lookup(apiurl, opts.project, opts.force_refresh, quiet=opts.format != 'plain')

    if opts.format != 'plain':
        # Suppliment data with request information.
        requests = get_request_list(apiurl, opts.project, None, None, ['new', 'review'], 'submit')
        requests.extend(get_request_list(apiurl, opts.project, None, None, ['new', 'review'], 'delete'))

        requests_map = {}
        for request in requests:
            for action in request.actions:
                requests_map[action.tgt_package] = request.reqid

        # Convert data from lookup to list.
        out = []
        for package, details in sorted(lookup.items()):
            out.append({
                'package': package,
                'origin': details['origin'],
                'revisions': details.get('revisions', []),
                'request': requests_map.get(package),
            })

        osrt_origin_dump(opts.format, out)
        return

    line_format = '{:<' + str(osrt_origin_max_key(lookup, 7)) + '}  {}'
    print(line_format.format('package', 'origin'))

    for package, details in sorted(lookup.items()):
        print(line_format.format(package, details['origin']))

def osrt_origin_package(apiurl, opts, *packages):
    origin_info = origin_find(apiurl, opts.project, packages[0])
    print(origin_info)

def osrt_origin_report_count(lookup):
    origin_count = {}
    for package, details in lookup.items():
        origin_count.setdefault(details['origin'], 0)
        origin_count[details['origin']] += 1

    return origin_count

def osrt_origin_report_count_diff(origin_count, origin_count_previous):
    origin_count_change = {}
    for origin, count in origin_count.items():
        delta = count - origin_count_previous.get(origin, 0)
        delta = '+' + str(delta) if delta > 0 else str(delta)
        origin_count_change[origin] = delta

    return origin_count_change

def osrt_origin_report_diff(lookup, lookup_previous):
    diff = {}
    for package, details in lookup.items():
        origin_previous = lookup_previous.get(package, {}).get('origin')
        if details['origin'] != origin_previous:
            diff[package] = (details['origin'], origin_previous)

    return diff

def osrt_origin_report(apiurl, opts, *args):
    lookup = osrt_origin_lookup(apiurl, opts.project, opts.force_refresh)
    origin_count = osrt_origin_report_count(lookup)

    columns = ['origin', 'count', 'percent']
    column_formats = [
        '{:<' + str(osrt_origin_max_key(origin_count, 6)) + '}',
        '{:>5}',
        '{:>7}',
    ]

    if opts.diff:
        columns.insert(2, 'change')
        column_formats.insert(2, '{:>6}')

        lookup_previous = osrt_origin_lookup(apiurl, opts.project, previous=True)
        if lookup_previous is not None:
            origin_count_previous = osrt_origin_report_count(lookup_previous)
            origin_count_change = osrt_origin_report_count_diff(origin_count, origin_count_previous)
            package_diff = osrt_origin_report_diff(lookup, lookup_previous)
        else:
            origin_count_change = {}
            package_diff = []

    line_format = '  '.join(column_formats)
    report = [line_format.format(*columns)]

    total = len(lookup)
    for origin, count in sorted(origin_count.items(), key=lambda x : x[1], reverse=True):
        values = [origin, count, round(float(count) / total * 100, 2)]
        if opts.diff:
            values.insert(2, origin_count_change.get(origin, 0))
        report.append(line_format.format(*values))

    if opts.diff and len(package_diff):
        line_format = '{:<' + str(osrt_origin_max_key(package_diff, 7)) + '}  ' + \
            '  '.join([column_formats[0]] * 2)
        report.append('')
        report.append(line_format.format('package', 'origin', 'origin previous'))
        for package, origins in sorted(package_diff.items()):
            report.append(line_format.format(package, *origins))


    body = '\n'.join(report)
    print(body)

    if opts.mail:
        mail_send(apiurl, opts.project, 'release-list', '{} origin report'.format(opts.project),
                  body, None, dry=opts.dry)
