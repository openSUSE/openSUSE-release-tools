#!/usr/bin/python
# Copyright (c) 2015 SUSE Linux Products GmbH
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

import os
import sys
from datetime import datetime
from sqlalchemy import Column, ForeignKey, Integer, String, Boolean, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship, backref
from sqlalchemy.orm import sessionmaker
from sqlalchemy import create_engine

from abichecker_common import DATADIR

Base = declarative_base()

class Request(Base):
    __tablename__ = 'request'
    id = Column(Integer, primary_key=True)
    state = Column(String(32), nullable=False)
    result = Column(String(32), nullable=True)

    t_created = Column(DateTime, default=datetime.now)
    t_updated = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class Log(Base):
    __tablename__ = 'log'
    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey('request.id'), nullable=False)
    request = relationship(Request, backref=backref('log', order_by=id, cascade="all, delete-orphan"))
    line = Column(Text(), nullable=True)

    t_created = Column(DateTime, default=datetime.now)

class ABICheck(Base):
    __tablename__ = 'abicheck'
    id = Column(Integer, primary_key=True)
    request_id = Column(Integer, ForeignKey('request.id'), nullable=False)
    request = relationship(Request, backref=backref('abichecks', order_by=id, cascade="all, delete-orphan"))

    src_project = Column(String(255), nullable=False)
    src_package = Column(String(255), nullable=False)
    src_rev = Column(String(255), nullable=True)
    dst_project = Column(String(255), nullable=False)
    dst_package = Column(String(255), nullable=False)
    result = Column(Boolean(), nullable = False)

    t_created = Column(DateTime, default=datetime.now)
    t_updated = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class LibReport(Base):
    __tablename__ = 'libreport'
    id = Column(Integer, primary_key=True)
    submission_id = Column(Integer, ForeignKey('abicheck.id'), nullable=False)
    abicheck = relationship(ABICheck, backref=backref('reports', order_by=id, cascade="all, delete-orphan"))

    src_repo = Column(String(255), nullable=False)
    src_lib = Column(String(255), nullable=False)
    dst_repo = Column(String(255), nullable=False)
    dst_lib = Column(String(255), nullable=False)
    arch = Column(String(255), nullable=False)
    htmlreport = Column(String(255), nullable=False)
    result = Column(Boolean(), nullable = False)

    t_created = Column(DateTime, default=datetime.now)
    t_updated = Column(DateTime, default=datetime.now, onupdate=datetime.now)

def db_engine():
    return create_engine('sqlite:///%s/abi-checker.db'%DATADIR)

def db_session():
    engine = db_engine()
    Base.metadata.bind = engine
    DBSession = sessionmaker(bind=engine)
    return DBSession()
