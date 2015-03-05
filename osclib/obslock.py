# Copyright (C) 2015 SUSE Linux GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from datetime import datetime
import time
import warnings
from xml.etree import cElementTree as ET

from osc import conf
from osc.core import makeurl
from osc.core import http_GET
from osc.core import http_POST


class OBSLock(object):
    """Implement a distributed lock using a shared OBS resource."""

    def __init__(self, apiurl, project, ttl=3600):
        self.apiurl = apiurl
        self.project = project
        self.lock = conf.config[project]['lock']
        self.ns = conf.config[project]['lock-ns']
        # TTL is measured in seconds
        self.ttl = ttl
        self.user = conf.config['api_host_options'][apiurl]['user']
        self.locked = False

    def _signature(self):
        """Create a signature with a timestamp."""
        return '%s@%s' % (self.user, datetime.isoformat(datetime.utcnow()))

    def _parse(self, signature):
        """Parse a signature into an user and a timestamp."""
        user, ts = None, None
        try:
            user, ts_str = signature.split('@')
            ts = datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S.%f')
        except (AttributeError, ValueError):
            pass
        return user, ts

    def _read(self):
        url = makeurl(self.apiurl, ['source', self.lock, '_attribute', '%s:LockedBy' % self.ns])
        root = ET.parse(http_GET(url)).getroot()
        signature = None
        try:
            signature = root.find('.//value').text
        except (AttributeError, ValueError):
            pass
        return signature

    def _write(self, signature):
        url = makeurl(self.apiurl, ['source', self.lock, '_attribute', '%s:LockedBy' % self.ns])
        data = """
        <attributes>
          <attribute namespace='%s' name='LockedBy'>
            <value>%s</value>
          </attribute>
        </attributes>""" % (self.ns, signature)
        http_POST(url, data=data)

    def acquire(self):
        # If the project doesn't have locks configured, raise a
        # Warning (but continue the operation)
        if not self.lock:
            warnings.warn('Locking attribute is not found.  Create one to avoid race conditions.')
            return self

        user, ts = self._parse(self._read())
        if user and ts:
            now = datetime.utcnow()
            if now < ts:
                raise Exception('Lock acquired from the future [%s] by [%s]. Try later.' % (ts, user))
            if (now - ts).seconds < self.ttl:
                print 'Lock acquired by [%s]. Try later.' % user
                exit(-1)
                # raise Exception('Lock acquired by [%s]. Try later.' % user)
        self._write(self._signature())

        time.sleep(1)
        user, ts = self._parse(self._read())
        if user != self.user:
            raise Exception('Race condition, [%s] wins. Try later.' % user)

        return self

    def release(self):
        # If the project do not have locks configured, simply ignore
        # the operation.
        if not self.lock:
            return

        user, ts = self._parse(self._read())
        if user == self.user:
            self._write('')

    __enter__ = acquire

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
