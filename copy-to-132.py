#!/usr/bin/python

#sys.path.append(os.path.expanduser('~/.osc-plugins'))

import sys
import os
import osc
import osc.core
import osc.conf
import xml.etree.ElementTree as ET
import re

os.environ['OSC_CONFIG'] = os.path.expanduser('~/.oscrc-syncer')

#initialize osc config
osc.conf.get_config()
osc.conf.config['debug'] = True

srcmd5s = dict()
revs = dict()

def parse_prj(prj):
    url = osc.core.makeurl(osc.conf.config['apiurl'], ['source', prj], { 'view': 'info', 'nofilename': 1 } )
    f = osc.core.http_GET(url)
    root = ET.parse(f)
    
    ret = dict()

    for si in root.findall('./sourceinfo'):
        if si.attrib.has_key('lsrcmd5'):
            continue # ignore links
        package = si.attrib['package']
        md5 = si.attrib['verifymd5']
        srcmd5s[md5] = si.attrib['srcmd5']
        revs[md5] = si.attrib['rev']
        if re.match('_product.*', package):
            continue
        ret[package] = md5

    return ret

# POSIX system. Create and return a getch that manipulates the tty.
import termios, sys, tty
def _getch():

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return ch

def get_devel_project(package):
    url = osc.core.makeurl(osc.conf.config['apiurl'], ['source', 'openSUSE:Factory', package, '_meta'])
    f = osc.core.http_GET(url)
    root = ET.parse(f)
    for dp in root.findall('./devel'):
        return dp.attrib['project']

    raise 'NOW WHAT?'

def create_submit(project=None, package=None, rev=None, md5=None):
    text  = "<request type='submit'>\n"
    text += " <submit>\n"
    text += " <source project='" + project + "' package='" + package + "' rev='" + md5 + "'/>\n"
    text += " <target project='openSUSE:13.2' package='" + package + "'/>\n"
    text += " </submit>\n"
    text += " <description>Submit revision " + rev + " of openSUSE:Factory/" + package + "</description>\n"
    text += "</request>"

    url = osc.core.makeurl(osc.conf.config['apiurl'], [ 'request' ], { 'cmd': 'create' })
    #print text
    print osc.core.http_POST(url, data=text).read()

def create_delete(package):
    text  = "<request>\n"
    text += " <action type='delete'>\n"
    text += " <target project='openSUSE:13.2' package='" + package + "'/>\n"
    text += " </action>\n"
    text += " <description>Gone from Factory</description>\n"
    text += "</request>"

    url = osc.core.makeurl(osc.conf.config['apiurl'], [ 'request' ], { 'cmd': 'create' })
    #print text
    print osc.core.http_POST(url, data=text).read()

def load_nos():
    ret = set()
    with open('nos') as f:
        for line in f.readlines():
            ret.add(line.strip())
    return ret

def save_nos(nos):
    with open('nos', 'w') as f:
        for key in nos:
            f.write(key + "\n")

def find_request_md5s(package):
    url = osc.core.makeurl(osc.conf.config['apiurl'], 
                           ['search', 'request'],
                           { 'match': "action/target/@project='openSUSE:13.2' and action/target/@package='{}'".format(package) })
    f = osc.core.http_GET(url)
    root = ET.parse(f).getroot()
    md5s = set()
    for rq in root.findall('request'):
        for source in rq.findall('.//source'):
            md5s.add(source.attrib['rev'])
    return md5s
        
factory = parse_prj('openSUSE:Factory')
d132 = parse_prj('openSUSE:13.2')

NOS = load_nos()

for package in sorted(set(factory) | set(d132)):
    prompt = None

    if factory.has_key(package):

        pmd5 = "%s-%s" % ( package, factory[package] )
        if pmd5 in NOS:
            continue

        if not d132.has_key(package):
            if srcmd5s[factory[package]] in find_request_md5s(package):
                continue
        elif factory[package] == d132[package]:
            continue
        elif srcmd5s[factory[package]] in find_request_md5s(package):
            print "%s already requested" % pmd5
            continue
        else:
            url = osc.core.makeurl(osc.conf.config['apiurl'], ['source', 'openSUSE:Factory', package], 
                                   { 'unified': 1, 'opackage': package, 'oproject': 'openSUSE:13.2', 'cmd': 'diff', 'expand': 1 } )
            difflines = osc.core.http_POST(url).readlines()
            inchanges = False
            for line in difflines:
                if re.match(r'^Index:.*\.changes', line):
                    inchanges = True
                elif re.match(r'^Index:', line):
                    inchanges = False
                
                if inchanges:
                    print line,

            prompt = "copy diffing package %s ?" % pmd5

        md5 = srcmd5s[factory[package]]
        rev = revs[factory[package]]
        devprj = get_devel_project(package)
        if devprj == 'openSUSE:Factory':
            continue
            
        print prompt
        d = _getch()
        if d == 'y':
            create_submit(project=devprj, package=package, rev=rev, md5=md5)
        elif d == 'n':
            NOS.add(pmd5)
            save_nos(NOS)

    else: # the 13.2 must have it
        print "delete package 13.2/%s-%s" % ( package, d132[package] )
        create_delete(package)

