# Copyright (C) 2014 SUSE Linux Products GmbH
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

import fcntl
import glob
import hashlib
import os.path
try:
    import cPickle as pickle
except:
    import pickle
import shelve
import shutil
import time
from UserDict import DictMixin


class PkgCache(DictMixin):
    def __init__(self, basecachedir, force_clean=False):
        self.cachedir = os.path.join(basecachedir, 'pkgcache')
        self.index_fn = os.path.join(self.cachedir, 'index.db')

        if force_clean:
            try:
                shutil.rmtree(self.cachedir)
            except OSError:
                pass

        if not os.path.exists(self.cachedir):
            os.makedirs(self.cachedir)

        self._clean_cache()

    def _lock(self, filename):
        """Get a lock for the index file."""
        lckfile = open(filename + '.lck', 'w')
        fcntl.flock(lckfile.fileno(), fcntl.LOCK_EX)
        return lckfile

    def _unlock(self, lckfile):
        """Release the lock for the index file."""
        fcntl.flock(lckfile.fileno(), fcntl.LOCK_UN)
        lckfile.close()

    def _open_index(self):
        """Open the index file for the cache / container."""
        lckfile = self._lock(self.index_fn)
        index = shelve.open(self.index_fn, protocol=-1)
        # Store a reference to the lckfile to avoid to be closed by gc
        index.lckfile = lckfile
        return index

    def _close_index(self, index):
        """Close the index file for the cache / container."""
        index.close()
        self._unlock(index.lckfile)

    def _clean_cache(self, ttl=14*24*60*60, index=None):
        """Remove elements in the cache that share the same prefix of the key
        (all except the mtime), and keep the latest one.  Also remove
        old entries based on the TTL.

        """
        _i = self._open_index() if index is None else index

        # Ugly hack to assure that the key is serialized always with
        # the same pickle format.  Pickle do not guarantee that the
        # same object is serialized always in the same string.
        skeys = {pickle.loads(key): key for key in _i}
        keys = sorted(skeys)

        now = int(time.time())
        last = None
        for key in keys:
            if last and last[:-1] == key[:-1]:
                self.__delitem__(key=skeys[last], skey=True, index=_i)
                last = key
            elif now - key[-1] >= ttl:
                self.__delitem__(key=skeys[key], skey=True, index=_i)
            else:
                last = key

        if index is None:
            self._close_index(_i)

    def __getitem__(self, key, index=None):
        """Get a element in the cache.

        For the container perspective, the key is a tuple like this:
        (project, repository, arch, package, filename, mtime)

        """
        _i = self._open_index() if index is None else index

        key = pickle.dumps(key, protocol=-1)
        value = pickle.loads(_i[key])

        if index is None:
            self._close_index(_i)

        return value

    def __setitem__(self, key, value, index=None):
        """Add a new file in the cache. 'value' is expected to contains the
        path of file.

        """
        _i = self._open_index() if index is None else index

        key = pickle.dumps(key, protocol=-1)

        original_value = value

        md5 = hashlib.md5(open(value, 'rb').read()).hexdigest()
        filename = os.path.basename(value)
        value = (md5, filename)
        value = pickle.dumps(value, protocol=-1)
        _i[key] = value

        # Move the file into the container using a hard link
        cache_fn = os.path.join(self.cachedir, md5[:2], md5[2:])
        if os.path.exists(cache_fn):
            # Manage collisions using hard links and refcount
            collisions = sorted(glob.glob(cache_fn + '-*'))
            next_refcount = 1
            if collisions:
                next_refcount = int(collisions[-1][-3:]) + 1
            next_cache_fn = cache_fn + '-%03d' % next_refcount
            os.link(cache_fn, next_cache_fn)
        else:
            dirname = os.path.dirname(cache_fn)
            if not os.path.exists(dirname):
                os.makedirs(dirname)
            os.link(original_value, cache_fn)

        if index is None:
            self._close_index(_i)

    def __delitem__(self, key, skey=False, index=None):
        """Remove a file from the cache."""
        _i = self._open_index() if index is None else index

        key = pickle.dumps(key, protocol=-1) if not skey else key
        value = pickle.loads(_i[key])

        md5, _ = value

        # Remove the file (taking care of collision) and the directory
        # if it is empty
        cache_fn = os.path.join(self.cachedir, md5[:2], md5[2:])
        collisions = sorted(glob.glob(cache_fn + '-*'))
        if collisions:
            os.unlink(collisions[-1])
        else:
            os.unlink(cache_fn)

        dirname = os.path.dirname(cache_fn)
        if not os.listdir(dirname):
            os.rmdir(dirname)

        del _i[key]

        if index is None:
            self._close_index(_i)

    def keys(self, index=None):
        _i = self._open_index() if index is None else index

        keys = [pickle.loads(key) for key in _i]

        if index is None:
            self._close_index(_i)

        return keys

    def linkto(self, key, target, index=None):
        """Create a link between the cached object and the target"""
        _i = self._open_index() if index is None else index

        md5, filename = self.__getitem__(key, index=_i)
        if filename != target:
            pass
            # print 'Warning. The target name (%s) is different from the original name (%s)' % (target, filename)
        cache_fn = os.path.join(self.cachedir, md5[:2], md5[2:])
        os.link(cache_fn, target)

        if index is None:
            self._close_index(_i)
