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

    def __init__(self, apiurl, project, ttl=3600, reason=None, needed=True):
        self.apiurl = apiurl
        self.project = project
        self.lock = conf.config[project]['lock']
        self.ns = conf.config[project]['lock-ns']
        # TTL is measured in seconds
        self.ttl = ttl
        self.user = conf.config['api_host_options'][apiurl]['user']
        self.reason = reason
        self.reason_sub = None
        self.locked = False
        self.needed = needed

    def _signature(self):
        """Create a signature with a timestamp."""
        reason = str(self.reason)
        if self.reason_sub:
            reason += ' ({})'.format(self.reason_sub)
        reason = reason.replace('@', 'at').replace('#', 'hash')
        return '%s#%s@%s' % (self.user, reason, datetime.isoformat(datetime.utcnow()))

    def _parse(self, signature):
        """Parse a signature into an user and a timestamp."""
        user, reason, reason_sub, ts = None, None, None, None
        try:
            rest, ts_str = signature.split('@')
            user, reason = rest.split('#')
            if ' (hold' in reason:
                reason, reason_sub = reason.split(' (', 1)
                reason_sub = reason_sub.rstrip(')')
            ts = datetime.strptime(ts_str, '%Y-%m-%dT%H:%M:%S.%f')
        except (AttributeError, ValueError):
            pass
        return user, reason, reason_sub, ts

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
        if not self.needed: return self

        # If the project doesn't have locks configured, raise a
        # Warning (but continue the operation)
        if not self.lock:
            warnings.warn('Locking attribute is not found.  Create one to avoid race conditions.')
            return self

        user, reason, reason_sub, ts = self._parse(self._read())
        if user and ts:
            now = datetime.utcnow()
            if now < ts:
                raise Exception('Lock acquired from the future [%s] by [%s]. Try later.' % (ts, user))
            delta = now - ts
            if delta.total_seconds() < self.ttl:
                # Existing lock that has not expired.
                stop = True
                if user == self.user:
                    if reason.startswith('hold'):
                        # Command being issued during a hold.
                        self.reason_sub = reason
                        stop = False
                    elif reason == 'lock':
                        # Second pass to acquire hold.
                        stop = False

                if stop:
                    print 'Lock acquired by [%s] %s ago, reason <%s>. Try later.' % (user, delta, reason)
                    exit(-1)
        self._write(self._signature())

        time.sleep(1)
        user, _, _, _ = self._parse(self._read())
        if user != self.user:
            raise Exception('Race condition, [%s] wins. Try later.' % user)
        self.locked = True

        return self

    def release(self, force=False):
        if not force and not self.needed: return

        # If the project do not have locks configured, simply ignore
        # the operation.
        if not self.lock:
            return

        user, reason, reason_sub, _ = self._parse(self._read())
        clear = False
        if user == self.user:
            if reason_sub:
                self.reason = reason_sub
                self.reason_sub = None
                self._write(self._signature())
            elif not reason.startswith('hold') or force:
                # Only clear a command lock as hold can only be cleared by force.
                clear = True
        elif user is not None and force:
            # Clear if a lock is present and force.
            clear = True

        if clear:
            self._write('')
            self.locked = False

    def hold(self, message=None):
        self.reason = 'hold'
        if message:
            self.reason += ': ' + message
        self.acquire()

    __enter__ = acquire

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
