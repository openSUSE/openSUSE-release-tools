#!/usr/bin/python

from __future__ import print_function

import sys
import os
import osc
import osc.core
import osc.conf
import xml.etree.ElementTree as ET
import re


results = []
repo = ""
architectures = ["x86_64", "i586"]
pkg = ""
projects = ['openSUSE:Factory', 'openSUSE:Factory:Rebuild']

#initialize osc config
osc.conf.get_config()

def get_prj_results(prj, arch):
    url = osc.core.makeurl(osc.conf.config['apiurl'], ['build', prj, 'standard', arch, "_jobhistory?code=lastfailures"])
    f = osc.core.http_GET(url)
    xml = f.read()
    results = []

    root = ET.fromstring(xml)

    xmllines = root.findall("./jobhist")

    for pkg in xmllines:
        if pkg.attrib['code'] == 'failed':
            results.append(pkg.attrib['package'])

    return results

def compare_results(factory, rebuild, testmode):

    com_res = set(rebuild).symmetric_difference(set(factory))

    if testmode != False:
        print(com_res)

    return com_res

def check_pkgs(rebuild_list):
    url = osc.core.makeurl(osc.conf.config['apiurl'], ['source', 'openSUSE:Factory'])
    f = osc.core.http_GET(url)
    xml = f.read()
    pkglist = []

    root = ET.fromstring(xml)

    xmllines = root.findall("./entry")

    for pkg in xmllines:
        if pkg.attrib['name'] in rebuild_list:
            pkglist.append(pkg.attrib['name'])

    return pkglist

def rebuild_pkg_in_factory(package, prj, arch, testmode, code=None):
    query = { 'cmd': 'rebuild', 'arch': arch }
    #prj = "home:jzwickl"
    if package:
        query['package'] = package
    pkg = query['package']

    u = osc.core.makeurl(osc.conf.config['apiurl'], ['build', prj], query=query)

    if testmode != False:
        print("Trigger rebuild for this package: " +  u)

    else:
        try:
            print('tried to trigger rebuild for project \'%s\' package \'%s\'' % (prj, pkg))
            f = osc.core.http_POST(u)

        except:
            print('could not trigger rebuild for project \'%s\' package \'%s\'' % (prj, pkg))

testmode = False
try:
    if sys.argv[1] != None:
        if sys.argv[1] == '-test':
            testmode = True
            print("testmode: "+str(testmode))
    else:
        testmode = False
except:
    pass

for arch in architectures:
    fact_result = get_prj_results('openSUSE:Factory', arch)
    rebuild_result = get_prj_results('openSUSE:Factory:Rebuild', arch)
    rebuild_result = check_pkgs(rebuild_result)
    fact_result = check_pkgs(fact_result)
    result = compare_results(fact_result, rebuild_result, testmode)

    print(sorted(result))

    for package in result:
        rebuild_pkg_in_factory(package, 'openSUSE:Factory', arch, testmode, None)
        rebuild_pkg_in_factory(package, 'openSUSE:Factory:Rebuild', arch, testmode, None)
