#!/usr/bin/python3

import argparse
import logging
from shutil import copyfile
import subprocess
from os.path import basename
import glob
from openqa_client.client import OpenQA_Client
from openqa_client.exceptions import ConnectionError, RequestError

def old_filename(state):
    return f'{args.repos}/{state}.yaml'

def new_filename(state):
    return f'{args.to}/{state}.yaml'

def file_changed(state):
    with open(old_filename(state), 'r') as old_file:
        old_content = old_file.read()
    try:
        with open(new_filename(state), 'r') as new_file:
            new_content = new_file.read()
    except FileNotFoundError:
        return True
    return old_content != new_content

def notify_project(openqa, state):
    project, repository = state.split('_-_')
    if not file_changed(state):
        logger.debug(f'{state} did not change')
        return
    try:
        openqa.openqa_request('PUT', 'obs_rsync/{}/runs?repository={}'.format(project, repository), retries=0)
    except RequestError as e:
        logger.info("Got exception on syncing repository: {}".format(e))
        return
    copyfile(old_filename(state), new_filename(state))
    subprocess.run(f'cd {args.to} && git add . && git commit -m "Update of {project}/{repository}" && git push', shell=True, check=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Bot to sync openQA status to OBS')
    parser.add_argument('--openqa', type=str, required=True, help='OpenQA URL')
    parser.add_argument('--repos', type=str, required=True, help='Directory to read from')
    parser.add_argument('--to', type=str, required=True, help='Directory to commit into')

    global args
    args = parser.parse_args()
    global logger
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    openqa = OpenQA_Client(server=args.openqa)

    interesting_repos = dict()
    list = openqa.openqa_request('GET', 'obs_rsync')
    for repopair in list:
        project, repository = repopair
        interesting_repos[f'{project}_-_{repository}'] = 1

    openqa = OpenQA_Client(server=args.openqa)
    for state in glob.glob('{}/*.yaml'.format(args.repos)):
        state = basename(state).replace('.yaml', '')
        if not state in interesting_repos:
            continue
        notify_project(openqa, state)
