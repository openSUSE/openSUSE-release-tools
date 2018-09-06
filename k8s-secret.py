#!/usr/bin/python

from __future__ import print_function
import argparse
import os
from osclib.cache_manager import CacheManager
import subprocess
import sys

CACHE_DIR = CacheManager.directory('k8s-secret')
SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))


def secret_create(cache_file):
    environment = {'OSCRC': cache_file}

    print('Username: ', end='')
    environment['OBS_USER'] = raw_input()

    print('Password: ', end='')
    environment['OBS_PASS'] = raw_input()

    osc_init = os.path.join(SCRIPT_PATH, 'dist/ci/osc-init')
    subprocess.Popen([osc_init], env=environment).wait()

def secret_apply(prefix, cache_file):
    print(subprocess.check_output([
        'kubectl', 'create', 'secret', 'generic',
        '{}-oscrc'.format(prefix), '--from-file={}={}'.format('.oscrc', cache_file)]))

def main(args):
    cache_file = os.path.join(CACHE_DIR, args.prefix)
    if not os.path.exists(cache_file) or args.create:
        secret_create(cache_file)

    with open(cache_file, 'r') as f:
        print(f.read())

    print('Apply secret for {} [y/n] (y): '.format(args.prefix), end='')
    response = raw_input().lower()
    if response != '' and response != 'y':
        return

    secret_apply(args.prefix, cache_file)

    if args.delete:
        os.remove(cache_file)

if __name__ == '__main__':
    description = 'Apply kubernetes secrets for OSRT tool osc configuration.'
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--create', action='store_true', help='create regardless of existing file')
    parser.add_argument('--delete', action='store_true', help='delete cached secret after application')
    parser.add_argument('prefix', help='prefix for which to create secret (ex. check-source, repo-checker)')
    args = parser.parse_args()

    sys.exit(main(args))
