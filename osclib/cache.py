from __future__ import print_function

import datetime
import hashlib
import os
import osc.core
import re
import shutil
import sys
import urlparse
from StringIO import StringIO
from osc import conf
from osc.core import urlopen
from time import time

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET


def http_request(method, url, headers={}, data=None, file=None):
    """
    Wrapper for osc.core.http_request() to provide GET request caching.
    """

    if method == 'GET':
        ret = Cache.get(url)
        if ret:
            return ret
    else:
        # Logically, seems to make more sense after real call, but practically
        # it should not matter and makes the apitests happy when dealing with
        # request acceptance which causes a GET to determine target project.
        Cache.delete(url)

    ret = osc.core._http_request(method, url, headers, data, file)

    if method == 'GET':
        ret = Cache.put(url, ret)

    return ret


class Cache(object):
    """
    Provide a cache implementation for osc.core.http_request().

    The cache takes a list of regular expression patterns and time to live (ttl)
    for API paths. In addition to the ttl the project context is taken into
    account when available in order to expire all caches related to a project
    when the remote server indicates a change was made more recently than the
    local cache reflects. This provides a fairly robust cache that can handle
    multiple users changing the same projects.

    Cannot safely cache, for lengthy periods, paths that can update without user
    interaction or that do not trigger the project updated timestamp to change.
    Such paths include anything related to build status. When a source package
    is updated the linked packages do not trigger an update of their project. As
    such sources cannot be reliably cached for too long.

    Any paths without a project context will be cleared when updated using this
    cache, but obviously not for other contributors.
    """

    CACHE_DIR = os.path.expanduser('~/.cache/osc-plugin-factory')
    TTL_LONG = 12 * 60 * 60
    TTL_SHORT = 5 * 60
    TTL_DUPLICATE = 3
    PATTERNS = {
        # Group members cannot be guaranteed, but change rarely.
        '/group/[^/?]+$': TTL_SHORT,
        # Clear target project cache upon request acceptance.
        '/request/(\d+)\?.*newstate=accepted': TTL_DUPLICATE,
        "/search/package\?match=\[@project='([^']+)'\]$": TTL_LONG,
        # Potentially expire the latest_updated since it will be the only way to
        # tell after an adi staging is removed. For now just cache the calls
        # that occur in rapid succession.
        "/search/project/id\?match=starts-with\(@name,'([^']+)\:'\)$": TTL_DUPLICATE,
        # List of all projects may change, but relevant ones rarely.
        '/source$': TTL_LONG,
        # Sources will be expired with project, could be done on package level.
        '/source/([^/?]+)(?:\?.*)?$': TTL_LONG,
        # Project will be marked changed when packages are added/removed.
        '/source/([^/]+)/_meta$': TTL_LONG,
        '/source/([^/]+)/(?:[^/]+)/(?:_meta|_link)$': TTL_LONG,
        '/source/([^/]+)/dashboard/[^/]+': TTL_LONG,
        # Handles clearing local cache on package deletes. Lots of queries like
        # updating project info, comment, and package additions.
        '/source/([^/]+)/(?:[^/?]+)(?:\?[^/]+)?$': TTL_LONG,
        # Presumably users are not interweaving in short windows.
        '/statistics/latest_updated': TTL_SHORT,
    }

    last_updated = {}

    @staticmethod
    def init():
        Cache.patterns = []
        for pattern in Cache.PATTERNS:
            Cache.patterns.append(re.compile(pattern))

        # Replace http_request with wrapper function which needs a stored
        # version of the original function to call.
        if not hasattr(osc.core, '_http_request'):
            osc.core._http_request = osc.core.http_request
            osc.core.http_request = http_request

    @staticmethod
    def get(url):
        match, project = Cache.match(url)
        if match:
            path = Cache.path(url, project, include_file=True)
            ttl = Cache.PATTERNS[match]

            if project:
                # Given project context check to see if project has been updated
                # remotely more recently than local cache.
                apiurl, _ = Cache.spliturl(url)
                Cache.last_updated_load(apiurl)

                # Use the project last updated timestamp if availabe, otherwise
                # the oldest record indicates the longest period that can be
                # guaranteed to have no changes.
                if project in Cache.last_updated[apiurl]:
                    unchanged_since = Cache.last_updated[apiurl][project]
                else:
                    unchanged_since = Cache.last_updated[apiurl]['__oldest']

                now = datetime.datetime.utcnow()
                unchanged_since = datetime.datetime.strptime(unchanged_since, '%Y-%m-%dT%H:%M:%SZ')
                history_span = now - unchanged_since

                # Treat non-existant cache as brand new for the sake of history
                # span check since it behaves as desired.
                age = 0
                directory = Cache.path(url, project)
                if os.path.exists(directory):
                    age = time() - os.path.getmtime(directory)

                # If history span is shorter than allowed cache life and the age
                # of the current cache is older than history span with no
                # changes the cache cannot be guaranteed. For example:
                #   ttl = 1 day
                #   history_span = 0.5 day
                #   age = 0.75
                # Cannot be guaranteed.
                ttl_delta = datetime.timedelta(seconds=ttl)
                age_delta = datetime.timedelta(seconds=age)
                if history_span < ttl_delta and age_delta > history_span:
                    Cache.delete_project(apiurl, project)

            if os.path.exists(path) and time() - os.path.getmtime(path) <= ttl:
                if conf.config['debug']: print('CACHE_GET', url, file=sys.stderr)
                return urlopen('file://' + path)
            else:
                reason = '(' + ('expired' if os.path.exists(path) else 'does not exist') + ')'
                if conf.config['debug']: print('CACHE_MISS', url, reason, file=sys.stderr)

        return None

    @staticmethod
    def put(url, data):
        match, project = Cache.match(url)
        if match:
            path = Cache.path(url, project, include_file=True, makedirs=True)

            # Since urlopen does not return a seekable stream it cannot be reset
            # after writing to cache. As such a wrapper must be used. This could
            # be replaced with urlopen('file://...') to be consistent, but until
            # the need arrises StringIO has less overhead.
            text = data.read()
            data = StringIO(text)

            if conf.config['debug']: print('CACHE_PUT', url, project, file=sys.stderr)
            f = open(path,'w')
            f.write(text)
            f.close()

        return data

    @staticmethod
    def delete(url):
        match, project = Cache.match(url)
        if match:
            path = Cache.path(url, project, include_file=True)

            # Rather then wait for last updated statistics to expire, remove the
            # project cache if applicable.
            if project:
                apiurl, _ = Cache.spliturl(url)
                if project.isdigit():
                    # Clear target project cache upon request acceptance.
                    project = osc.core.get_request(apiurl, project).actions[0].tgt_project
                Cache.delete_project(apiurl, project)

            if os.path.exists(path):
                if conf.config['debug']: print('CACHE_DELETE', url, file=sys.stderr)
                os.remove(path)

        # Also delete version without query. This does not handle other
        # variations using different query strings. Handy for PUT with ?force=1.
        o = urlparse.urlsplit(url)
        if o.query != '':
            url_plain = urlparse.SplitResult(o.scheme, o.netloc, o.path, '', o.fragment).geturl()
            Cache.delete(url_plain)

    @staticmethod
    def delete_project(apiurl, project):
        path = Cache.path(apiurl, project)

        if os.path.exists(path):
            if conf.config['debug']: print('CACHE_DELETE_PROJECT', apiurl, project, file=sys.stderr)
            shutil.rmtree(path)

    @staticmethod
    def delete_all():
        if os.path.exists(Cache.CACHE_DIR):
            shutil.rmtree(Cache.CACHE_DIR)

    @staticmethod
    def match(url):
        apiurl, path = Cache.spliturl(url)
        for pattern in Cache.patterns:
            match = pattern.match(path)
            if match:
                return (pattern.pattern,
                        match.group(1) if len(match.groups()) > 0 else None)
        return (False, None)

    @staticmethod
    def spliturl(url):
        o = urlparse.urlsplit(url)
        apiurl = urlparse.SplitResult(o.scheme, o.netloc, '', '', '').geturl()
        path = urlparse.SplitResult('', '', o.path, o.query, '').geturl()
        return (apiurl, path)

    @staticmethod
    def path(url, project, include_file=False, makedirs=False):
        parts = [Cache.CACHE_DIR]

        o = urlparse.urlsplit(url)
        parts.append(o.hostname)

        if project:
            parts.append(project)

        directory = os.path.join(*parts)
        if not os.path.exists(directory) and makedirs:
            os.makedirs(directory)

        if include_file:
            parts.append(hashlib.sha1(url).hexdigest())
            return os.path.join(*parts)

        return directory

    @staticmethod
    def last_updated_load(apiurl):
        if apiurl in Cache.last_updated:
            return

        url = osc.core.makeurl(apiurl, ['statistics', 'latest_updated'], {'limit': 5000})
        root = ET.parse(osc.core.http_GET(url)).getroot()
        last_updated = {}
        for entity in root:
            # Entities repesent either a project or package.
            key = 'name' if entity.tag == 'project' else 'project'
            if entity.attrib[key] not in last_updated:
                last_updated[entity.attrib[key]] = entity.attrib['updated']

        # Keep track of the last entry to indicate the covered timespan.
        last_updated['__oldest'] = entity.attrib['updated']
        Cache.last_updated[apiurl] = last_updated
