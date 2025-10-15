#!/usr/bin/env python3
import sys
import os
import argparse
import json
import subprocess
import requests
import logging
import datetime
from urllib.parse import urlencode, urlunparse, urlparse
from lxml import etree as ET
from collections import namedtuple
import osc.core

USER_AGENT = "manual-trigger.py (https://github.com/os-autoinst/scripts)"
dry_run = False

log = logging.getLogger(sys.argv[0] if __name__ == "__main__" else __name__)
log.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
formatter = logging.Formatter(
    "%(name)-2s %(levelname)-2s %(funcName)s:%(lineno)d: %(message)s"
)
handler.setFormatter(formatter)
log.addHandler(handler)

CONFIG_DATA = {
    "products/PackageHub": "openSUSE:Backports:SLE-{version}:PullRequest:{pr_id}",
    "openSUSE/Leap": "openSUSE:Leap:{version}:PullRequest:{pr_id}",
    "openSUSE/LeapNonFree": "openSUSE:Leap:{version}:NonFree:PullRequest:{pr_id}",
}
GITEA_HOST = None
BS_HOST = None
REPO_PREFIX = None


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--myself", help="Username of bot", default="qam-openqa")
    parser.add_argument(
        "--review-group",
        help="Group to be used for approval",
        default="@qam-openqa-review",
    )
    parser.add_argument(
        "--openqa-host", help="OpenQA instance url", default="http://localhost:9526"
    )
    parser.add_argument(
        "--verbose", help="Verbosity", default="1", type=int, choices=[0, 1, 2, 3]
    )
    parser.add_argument("--branch", help="Target branch, eg. leap-16.0")
    parser.add_argument("--project", help="Target project")
    parser.add_argument("--pr-id", help="PR to trigger tests for")
    parser.add_argument(
        "--gitea", help="Gitea instance to use", default="https://src.opensuse.org"
    )
    parser.add_argument(
        "--bs", help="Build service api", default="https://api.opensuse.org"
    )
    parser.add_argument(
        "--bs-bot", help="Build service bot", default="autogits_obs_staging_bot"
    )
    parser.add_argument(
        "--repo-prefix",
        help="Build service repository",
        default="http://download.opensuse.org/repositories",
    )

    args = parser.parse_args()
    return args


def process_project(args):
    pull_requests = get_open_prs_for_project_branch(args.project, args.branch)
    for req in pull_requests:
        process_pull_request(req, args)

    log.info("Finished, processed %d pull requests", len(pull_requests))


def get_open_prs_for_project_branch(project, branch):
    pull_requests_url = (
        GITEA_HOST + f"/api/v1/repos/{project}/pulls?state=open&base_branch={branch}"
    )

    try:
        pull_requests = request_get(pull_requests_url)
    except requests.exceptions.HTTPError as e:
        log.error(f"Project '{project}' doesn't exist: {e}")
        return []

    if not pull_requests:
        log.warning(f"No pull requests found for '{project}' on'{branch}'")
        return []

    pr_numbers = [req["number"] for req in pull_requests]
    num_prs = len(pr_numbers)
    log.debug(f"Found {num_prs} pull requests for '{project}' on'{branch}'")
    return pr_numbers


def process_pull_request(pr_id, args):
    data = gitea_query_pr(args.project, pr_id)

    pr = data["number"]
    project = data["base"]["repo"]["full_name"]
    branch = data["base"]["label"]
    log.info(f"working on {project}#{pr}")

                )
                exit()

        except IOError as e:
            log.error(f"Error saving file: {e}")

    if branch == args.branch and project == args.project:
        obs_project, bs_repo_url = get_obs_values(project, branch, pr)
        # We need to query every package in the staged update
        packages_in_project = get_packages_from_obs_project(obs_project)
        if packages_in_project:
            settings = prepare_update_settings(
                obs_project, bs_repo_url, pr, packages_in_project
            )
            openqa_job_params = prepare_openqa_job_params(
                args, obs_project, data, settings
            )
            openqa_build_overview = openqa_schedule(args, openqa_job_params)
            log.info(f"Build triggered, results at {openqa_build_overview}")
    else:
        log.error(f"PR {project}#{pr} does not target {args.branch}")
def get_build_review_status(project, pr, review_id):
    return gitea_get_review(project, pr, review_id)


def prepare_update_settings(obs_project, bs_repo_url, pr, packages):
    settings = {}
    staged_update_name = get_staged_update_name(obs_project)
    # this could also be: obs_project.split(':')[-1]
    # start with a colon so it looks cool behind 'Build' :/
    settings["BUILD"] = f":{pr}:{staged_update_name}"
    patch_id = pr
    settings["INCIDENT_REPO"] = bs_repo_url
    settings["INCIDENT_PATCH"] = patch_id

    # openSUSE:Maintenance key
    settings["IMPORT_GPG_KEYS"] = "gpg-pubkey-b3fd7e48-5549fd0f"
    settings["ZYPPER_ADD_REPO_PREFIX"] = "staged-updates"

    settings["INSTALL_PACKAGES"] = " ".join(packages.keys())
    settings["VERIFY_PACKAGE_VERSIONS"] = " ".join(
        [f"{p.name} {p.version}-{p.release}" for p in packages.values()]
    )

    return settings


def get_staged_update_name(obs_project):
    query = {"deleted": 0}
    url = osc.core.makeurl(BS_HOST, ("source", obs_project), query=query)
    root = ET.parse(osc.core.http_GET(url)).getroot()
    source_packages = [n.attrib["name"] for n in root.findall("entry")]

    # In theory every staged update, has a single package
    if len(source_packages) > 1:
        raise MultipleSourcePackagesError("Multiple packages detected")
    elif len(source_packages) == 0:
        raise NoSourcePackagesError("No packages detected")
    else:
        return source_packages[0]


def get_obs_values(project, branch, pr_id):
    log.debug("Prepare obs url")
    template = CONFIG_DATA[project]
    # Version string has to be extracted from branch name
    branch_version = branch.split("-")[-1]
    obs_project = template.format(version=branch_version, project=project, pr_id=pr_id)
    target_repo = REPO_PREFIX + "/"
    target_repo += obs_project.replace(":", ":/")
    log.info(f"Target project {obs_project}, {target_repo}")
    return obs_project, target_repo


def get_packages_from_obs_project(obs_project):
    log.debug("Query packages in obs")
    packages = dict()
    # repository = osc api /build/{obs_project}
    # arches = osc api /build/{obs_project}/standard
    # arch = osc api /build/{obs_project}/standard/{arch}
    # for arch in arches:
    #   packages = osc api /build/{obs_project}/{repo}/{arch}/_repository?nosource=1
    #   for package in packages:
    #     get_package_deails = osc api /build/{obs_project}/standard/aarch64/_repository/opi.rpm?view=fileinfo

    repo = "standard"
    # osc api /build/{obs_project}/standard
    url = osc.core.makeurl(BS_HOST, ("build", obs_project, repo))
    root = ET.parse(osc.core.http_GET(url)).getroot()
    for arch in [n.attrib["name"] for n in root.findall("entry")]:
        query = {"nosource": 1}
        # packages/binary = osc api /build/{obs_project}/{repo}/{arch}/_repository?nosource=1
        url = osc.core.makeurl(
            BS_HOST, ("build", obs_project, repo, arch, "_repository"), query=query
        )
        root = ET.parse(osc.core.http_GET(url)).getroot()

        for binary in root.findall("binary"):
            b = binary.attrib["filename"]
            if b.endswith(".rpm"):
                # get_package_deails = osc api /build/{obs_project}/standard/aarch64/_repository/opi.rpm?view=fileinfo
                p = get_package_details(obs_project, repo, arch, b)
                packages[p.name] = p

    return packages


Package = namedtuple("Package", ("name", "version", "release"))


def get_package_details(prj, repo, arch, binary):
    url = osc.core.makeurl(
        BS_HOST,
        ("build", prj, repo, arch, "_repository", binary),
        query={"view": "fileinfo"},
    )
    root = ET.parse(osc.core.http_GET(url)).getroot()
    return Package(
        root.find(".//name").text,
        root.find(".//version").text,
        root.find(".//release").text,
    )


def gitea_query_pr(project, pr_id):
    log.debug("============== gitea_query_pr")
    pull_request_url = GITEA_HOST + f"/api/v1/repos/{project}/pulls/{pr_id}"
    return request_get(pull_request_url)


def gitea_post_status(job_params, job_url):
    log.debug("============== gitea_post_status")
    statuses_url = job_params["repo_api_url"] + "/statuses/" + job_params["sha"]

    payload = {
        "context": "qam-openqa",
        "description": "openQA check",
        "state": "pending",
        "target_url": job_url,
    }
    request_post(statuses_url, payload)


def gitea_get_review(project, pr_id, review_id):
    log.debug("============== gitea_get_review")
    review_url = (
        GITEA_HOST + f"/api/v1/repos/{project}/pulls/{pr_id}/reviews/{review_id}"
    )
    return request_get(review_url)

def get_events_by_timeline(project, pr_id):
    log.debug("============== get_events_by_timeline")
    url = GITEA_HOST + f"/api/v1/repos/{project}/issues/{pr_id}/timeline"
    request = request_get(url)

    # if request.status_code == 404:
    #     self.logger.error(f"'{self}' does not have a timeline")
    #     # this should throw an exception
    #     return

    timeline = request
    timeline.reverse()

    events = {}
    # reset the timeline every time a pull_push event happens
    for event in timeline:
        if event["type"] == "pull_push":
            log.debug(
                f"*** All events since last push ({event['body']}) have been processed for {project}#{pr_id}"
            )
            break

        user_login = event["user"]["login"]
        event_type = event["type"]

        if user_login not in events:
            events[user_login] = {}

        if event_type not in events[user_login]:
            log.debug(
                f"Storing most recent '{event_type}' for '{user_login}' (ID: {event['id']})"
            )
            events[user_login][event_type] = event
        else:
            log.debug(
                f"Skipping older '{event_type}' for '{user_login}' (ID: {event['id']})"
            )

    return events

def request_post(url, payload):
    log.debug(f"Posting request to gitea for {url}")
    log.debug(payload)
    token = os.environ.get("GITEA_TOKEN")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": "token " + token,
    }
    try:
        content = requests.post(url, headers=headers, data=payload)
        content.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error("Error while fetching %s: %s" % (url, str(e)))
        raise (e)


def request_get(url):
    log.debug(f"Sending request to gitea for {url}")
    token = os.environ.get("GITEA_TOKEN")
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Authorization": "token " + token,
    }
    try:
        content = requests.get(url, headers=headers)
        content.raise_for_status()
    except requests.exceptions.RequestException as e:
        log.error("Error while fetching %s: %s" % (url, str(e)))
        raise (e)
    json_data = content.json()
    return json_data


def prepare_openqa_job_params(args, obs_project, data, settings):
    log.debug("create_openqa_job_params")
    statuses_url = (
        GITEA_HOST
        + f"/api/v1/repos/{data['head']['repo']['full_name']}/statuses/{data['head']['sha']}"
    )
    params = {
        "PRIO": "100",
        # add "target URL" for the "Details" button of the CI status
        "CI_TARGET_URL": args.openqa_host,
        # set Gitea parameters so the Minion job will be able to report the status back to Gitea
        "GITEA_REPO": data["head"]["repo"]["full_name"],
        "GITEA_SHA": data["head"]["sha"],
        "GITEA_STATUSES_URL": statuses_url,
        "GITEA_PR_URL": data["html_url"],
        "webhook_id": "gitea:pr:" + str(data["number"]),
        "VERSION": data["base"]["label"].split("-")[-1],
        "DISTRI": "opensuse",  # there must be a better way than to hardcode
        "FLAVOR": "staged-updates",
        "ARCH": "x86_64",
    }
    return params | settings


def openqa_cli(host, subcommand, cmds, dry_run=False):
    log.debug("============== openqa_cli")
    client_args = [
        "openqa-cli",
        subcommand,
        "--host",
        host,
    ] + cmds
    log.debug("openqa_cli: %s %s" % (subcommand, client_args))
    res = subprocess.run(
        (["echo", "Simulating: "] if dry_run else []) + client_args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if len(res.stderr):
        log.warning(f"openqa_cli() {subcommand} stderr: {res.stderr}")
    res.check_returncode()
    return res.stdout.decode("utf-8")


def openqa_schedule(args, params):
    log.debug("============== openqa_schedule")

    cmd_args = []
    for key in params:
        cmd_args.append(f"{key}={params[key]}")
    openqa_cli(args.openqa_host, "schedule", cmd_args, dry_run)

    query_parameters = {
        "build": params["BUILD"],
        "distri": params["DISTRI"],
        "version": params["VERSION"],
    }

    base_url = urlparse(args.openqa_host + "/tests/overview")
    query_string = urlencode(query_parameters)
    test_overview_url = urlunparse(base_url._replace(query=query_string))
    return test_overview_url


class NoSourcePackagesError(Exception):
    pass


if __name__ == "__main__":
    args = parse_args()

    ret = os.environ.get("GITEA_TOKEN")
    if ret is None:
        raise RuntimeError("Environment variable GITEA_TOKEN is not set")

    GITEA_HOST = args.gitea
    BS_HOST = args.bs
    REPO_PREFIX = args.repo_prefix
    osc.conf.get_config()

    trigger_tests_for_pr(args)
    # if args.simulate_review_requested_event:
    #     simulate(args)
    # elif(args.simulate_build_finished_event and args.build_bot):
    #     simulate_build_finished_event(args)
    # else:
    #     listen(args)
    if args.pr_id:
        process_pull_request(args.pr_id, args)
    else:
        process_project(args)
