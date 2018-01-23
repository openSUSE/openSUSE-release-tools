#!/usr/bin/python
# Copyright (c) 2015 SUSE Linux GmbH
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from xdg.BaseDirectory import save_cache_path, save_data_path

CACHEDIR = save_cache_path('opensuse.org', 'abi-checker')
DATADIR = save_data_path('opensuse.org', 'abi-checker')

import abichecker_dbmodel as DB
import sqlalchemy.orm.exc

class Config(object):
    def __init__(self, session):
        self.session = session
        if self.session is None:
            self.session = DB.db_session()

    def set(self, key, value):
        try:
            entry = self.session.query(DB.Config).filter(DB.Config.key == key).one()
            entry.value = value
        except sqlalchemy.orm.exc.NoResultFound as e:
            entry = DB.Config(key=key, value=value)
        self.session.add(entry)
        self.session.commit()

    def get(self, key, default = None):
        try:
            entry = self.session.query(DB.Config).filter(DB.Config.key == key).one()
            return entry.value
        except sqlalchemy.orm.exc.NoResultFound as e:
            pass
        return default

    def delete(self, key):
        try:
            entry = self.session.query(DB.Config).filter(DB.Config.key == key).one()
            self.session.delete(entry)
            self.session.commit()
            return True
        except sqlalchemy.orm.exc.NoResultFound as e:
            pass
        return False

    def settings(self):
        for entry in self.session.query(DB.Config).all():
            yield (entry.key, entry.value)

