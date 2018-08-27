#!/usr/bin/python

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

