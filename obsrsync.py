#!/usr/bin/python3

import argparse
import glob
import json
import logging
import os
import random
import requests

import configparser

import osc
from osc.core import http_GET, makeurl
from osclib.core import target_archs
from osclib.PubSubConsumer import PubSubConsumer
from lxml import etree as ET
from urllib.error import HTTPError

from openqa_client.client import OpenQA_Client
from openqa_client.exceptions import RequestError

from urllib.parse import urlparse, urlunparse


class Configurable_OpenQA_Client(OpenQA_Client):
    def __init__(
        self, server: str = "", scheme: str = "", retries: int = 5, wait: int = 10
    ) -> None:
        self.retries = retries
        self.wait = wait
        # Read in config files.
        openqa_config = os.environ.get("OPENQA_CONFIG")
        config = configparser.ConfigParser()
        if openqa_config and os.path.exists(openqa_config):
            config.read(openqa_config)
        else:
            paths = ("/etc/openqa", f"{os.path.expanduser('~')}/.config/openqa")
            config.read(f"{path}/client.conf" for path in paths)

        # If server not specified, default to the first one in the
        # configuration file. If no configuration file, default to
        # localhost. NOTE: this is different from the perl client, it
        # *always* defaults to localhost.
        if not server:
            try:
                server = config.sections()[0]
            except (configparser.MissingSectionHeaderError, IndexError):
                server = "localhost"

        if server.startswith("http"):
            # Handle entries like [http://foo] or [https://foo]. The,
            # perl client does NOT handle these, so you shouldn't use
            # them. This client started out supporting this, though,
            # so it should continue to.
            if not scheme:
                scheme = urlparse(server).scheme
            server = urlparse(server).netloc

        if not scheme:
            if server in ("localhost", "127.0.0.1", "::1"):
                # Default to non-TLS for localhost; cert is unlikely to
                # be valid for 'localhost' and there's no MITM...
                scheme = "http"
            else:
                scheme = "https"

        self.baseurl = urlunparse((scheme, server, "", "", "", ""))

        # Get the API secrets from the config file.
        try:
            apikey = config.get(server, "key")
            self.apisecret = config.get(server, "secret")
        except configparser.Error:
            try:
                apikey = config.get(self.baseurl, "key")
                self.apisecret = config.get(self.baseurl, "secret")
            except configparser.Error:
                raise Exception(f"No API key for {server}: only GET requests will be allowed")

        # Create a Requests session and ensure some standard headers
        # will be used for all requests run through the session.
        self.session: requests.Session = requests.Session()
        headers = {}
        headers["Accept"] = "json"
        if apikey:
            headers["X-API-Key"] = apikey
        self.session.headers.update(headers)


class Listener(PubSubConsumer):
    def __init__(
        self, apiurl, amqp_prefix, namespaces, openqa_url, state_dir, dry_run=False
    ):
        super(Listener, self).__init__(amqp_prefix, logging.getLogger(__name__))
        self.apiurl = apiurl
        self.amqp_prefix = amqp_prefix
        self.namespaces = namespaces
        self.state_dir = state_dir
        self.dry_run = dry_run
        self.openqa = Configurable_OpenQA_Client(server=openqa_url)

        self.repositories_to_check = []
        self.repositories_to_monitor = set()

        os.makedirs(self.state_dir, exist_ok=True)
        self.interesting_repos = self.get_interesting_repos()

    def get_interesting_repos(self):
        interesting = set()
        try:
            repo_list = self.openqa.openqa_request("GET", "obs_rsync")
            for project, repository in repo_list:
                interesting.add(f"{project}_-_{repository}")
        except RequestError as e:
            self.logger.warning(f"Could not fetch interesting repos from openQA: {e}")
        return interesting

    def interval(self):
        if len(self.repositories_to_check) or len(self.repositories_to_monitor):
            return 5
        return super(Listener, self).interval()

    def still_alive(self):
        super(Listener, self).still_alive()
        self.interesting_repos = self.get_interesting_repos()
        self.check_some_repos()

    def routing_keys(self):
        return [self.amqp_prefix + ".obs.repo.build_finished"]

    def check_arch(self, project, repository, architecture):
        url = makeurl(
            self.apiurl,
            ["build", project, repository, architecture],
            {"view": "status"},
        )
        root = ET.parse(http_GET(url)).getroot()
        if root.get("code") == "finished":
            buildid = root.find("buildid")
            if buildid is not None:
                return buildid.text

        self.logger.info(
            f"{project}/{repository}/{architecture}: code=%s dirty=%s"
            % (root.get("code"), root.get("dirty", "false"))
        )
        if root.get("code") == "scheduling" or root.get("dirty", "false") == "true":
            self.repositories_to_monitor.add(f"{project}/{repository}")

    def check_all_archs(self, project, repository):
        ids = {}
        try:
            archs = target_archs(self.apiurl, project, repository)
        except HTTPError:
            return None

        for arch in archs:
            repoid = self.check_arch(project, repository, arch)
            if not repoid:
                self.logger.info(f"{project}/{repository}/{arch} not yet done")
                return None
            ids[arch] = repoid

        self.logger.info(f"All of {project}/{repository} finished")
        return ids

    def is_part_of_namespaces(self, project):
        if ":PullRequest:" in project:
            return False
        for namespace in self.namespaces:
            if project.startswith(namespace):
                return True
        return False

    def start_consuming(self):
        for namespace in self.namespaces:
            for state_file in glob.glob(
                os.path.join(self.state_dir, f"{namespace}*.json")
            ):
                state = os.path.basename(state_file).replace(".json", "")
                project, repository = state.split("_-_")
                if self.is_part_of_namespaces(project):
                    self.repositories_to_check.append([project, repository])
        self.check_some_repos()
        super(Listener, self).start_consuming()

    def check_some_repos(self):
        count = 0
        limit = 15
        while len(self.repositories_to_check):
            project, repository = self.repositories_to_check.pop()
            self.logger.debug(f"Check repo {project}/{repository}")
            self.update_repo(project, repository)
            count += 1
            if count >= limit:
                return

        repos = list(self.repositories_to_monitor)
        random.shuffle(repos)
        for entry in repos:
            self.repositories_to_monitor.discard(entry)
            project, repository = entry.split("/")
            self.logger.debug(f"Recheck repo {project}/{repository}")
            self.update_repo(project, repository)
            count += 1
            if count >= limit:
                return

    def update_repo(self, project, repository):
        ids = self.check_all_archs(project, repository)
        if not ids:
            return

        state_id = f"{project}_-_{repository}"
        state_file = os.path.join(self.state_dir, f"{state_id}.json")
        old_ids = {}

        if os.path.exists(state_file):
            with open(state_file, "r") as f:
                old_ids = json.load(f)

        if ids == old_ids:
            self.logger.debug(f"{state_id} did not change")
            return

        if state_id in self.interesting_repos:
            if self.dry_run:
                self.logger.info(
                    f"[DRY-RUN] Would trigger openQA obs_rsync for {project}/{repository}"
                )
            else:
                try:
                    self.logger.info(
                        f"Triggering openQA obs_rsync for {project}/{repository}"
                    )
                    self.openqa.openqa_request(
                        "PUT",
                        f"obs_rsync/{project}/runs?repository={repository}",
                        retries=0,
                    )
                except RequestError as e:
                    self.logger.error(
                        f"Got exception on syncing repository to openQA: {e}"
                    )
                    return

        if self.dry_run:
            self.logger.info(f"[DRY-RUN] Would save new state to {state_file}")
            return

        with open(state_file, "w") as f:
            json.dump(ids, f)

    def on_message(self, unused_channel, method, properties, body):
        self.logger.debug("on_message")
        self.acknowledge_message(method.delivery_tag)
        try:
            body = json.loads(body)
        except ValueError:
            return

        if method.routing_key.endswith(".obs.repo.build_finished"):
            if not self.is_part_of_namespaces(body["project"]):
                return
            self.restart_timer()
            self.logger.info(
                f"Repo finished event: {body['project']}/{body['repo']}/{body['arch']}"
            )
            self.update_repo(body["project"], body["repo"])
        else:
            self.logger.warning(f"unknown rabbitmq message {method.routing_key}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Daemon to monitor OBS via RabbitMQ and trigger openQA obs_rsync"
    )
    parser.add_argument(
        "--apiurl", "-A", type=str, required=True, help="API URL of OBS"
    )
    parser.add_argument("--openqa", type=str, required=True, help="OpenQA URL")
    parser.add_argument(
        "--state-dir",
        type=str,
        default="/var/lib/obs-openqa-sync",
        help="Directory to keep track of build states locally",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print actions without triggering openQA or saving state",
    )
    parser.add_argument(
        "--run-duration",
        type=int,
        default=10800,
        help="Seconds to run before exiting. Set to 0 to run indefinitely (default: 10800)",
    )
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        default=False,
        help="enable debug information",
    )
    parser.add_argument("namespaces", nargs="*", help="namespaces to wait for")

    args = parser.parse_args()

    osc.conf.get_config(override_apiurl=args.apiurl)
    osc.conf.config["debug"] = args.debug

    apiurl = osc.conf.config["apiurl"]
    amqp_prefix = "suse" if apiurl.endswith("suse.de") else "opensuse"

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)

    listener = Listener(
        apiurl, amqp_prefix, args.namespaces, args.openqa, args.state_dir, args.dry_run
    )

    try:
        listener.run(args.run_duration)
    except KeyboardInterrupt:
        listener.stop()
