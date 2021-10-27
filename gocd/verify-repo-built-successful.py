#!/usr/bin/python3

import argparse
import logging
import sys

import osc
from osc.core import http_GET, makeurl

from osclib.core import target_archs
from lxml import etree as ET

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Check if all packages built fine')
    parser.add_argument('--apiurl', '-A', type=str, help='API URL of OBS')
    parser.add_argument('-p', '--project', type=str, help='Project to check')
    parser.add_argument('-r', '--repository', type=str,
                        help='Repository to check')

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    apiurl = osc.conf.config['apiurl']

    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    # first check if repo is finished
    archs = target_archs(apiurl, args.project, args.repository)
    for arch in archs:
        url = makeurl(apiurl, ['build', args.project, args.repository, arch], {'view': 'status'})
        root = ET.parse(http_GET(url)).getroot()
        if root.get('code') == 'finished':
            continue
        logger.error('Repository {}/{}/{} is not yet finished'.format(args.project, args.repository, arch))
        logger.debug(ET.tostring(root).decode('utf-8'))
        sys.exit(1)

    # now check if all packages built fine
    url = makeurl(apiurl, ['build', args.project, '_result'],
                  {'view': 'summary', 'repository': args.repository})
    root = ET.parse(http_GET(url)).getroot()
    for count in root.findall('.//statuscount'):
        if int(count.get('count', 0)) == 0:
            continue
        if count.get('code') in ['succeeded', 'excluded', 'disabled']:
            continue
        logger.error('Repository {}/{} has {} packages'.format(args.project, args.repository, count.get('code')))
        sys.exit(1)
