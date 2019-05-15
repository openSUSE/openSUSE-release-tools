#!/usr/bin/python3

import argparse
from lxml import etree as ET
from osc import conf
from osc.core import meta_get_filelist
from osclib.core import package_binary_list
from osclib.core import source_file_load
import sys
import yaml


def getpackages(apiurl, project):
    packages = set()
    xml = ET.fromstring(source_file_load(
        apiurl, project, '000product', 'NON_FTP_PACKAGES.group'))
    packages.update(xml.xpath('//group/packagelist/package/@name'))

    return packages

def main(args):
    conf.get_config(override_apiurl=args.apiurl)
    conf.config['debug'] = args.debug
    apiurl = conf.config['apiurl']

    for p in sorted(getpackages(apiurl, args.project)):
        print('PublishFilter: {}-.*\\.rpm'.format(p))


if __name__ == '__main__':
    description = 'read NON_FTP_PACKAGES.group to create publish filter for prjconf'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('-A', '--apiurl', help='OBS instance API URL')
    parser.add_argument('-d', '--debug', action='store_true', help='print useful debugging info')
    parser.add_argument('project', help='project process')
    args = parser.parse_args()

    sys.exit(main(args))
