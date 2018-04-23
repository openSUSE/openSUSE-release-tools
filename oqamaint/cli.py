# -*- coding: utf-8 -*-

# standard library
import logging
import os.path as opa
import simplejson as json
import sys

# external dependency
from openqa_client.client import OpenQA_Client

# from package itself
import osc
from openqabot import OpenQABot
from opensuse import openSUSEUpdate
import ReviewBot
from suse import SUSEUpdate


class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, *args, **kwargs)
        self.clazz = OpenQABot

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)
        parser.add_option("--force", action="store_true",
                          help="recheck requests that are already considered done")
        parser.add_option("--no-comment", dest='comment', action="store_false",
                          default=True, help="don't actually post comments to obs")
        parser.add_option("--openqa", metavar='HOST', help="openqa api host")
        parser.add_option(
            "--data",
            default=opa.abspath(
                opa.dirname(
                    sys.argv[0])),
            help="Path to metadata dir (data/*.json)")
        return parser

    def _load_metadata(self):
        path = self.options.data
        project = {}

        with open(opa.join(path, "data/repos.json"), 'r') as f:
            target = json.load(f)

        with open(opa.join(path, "data/apimap.json"), 'r') as f:
            api = json.load(f)

        with open(opa.join(path, "data/incidents.json"), 'r') as f:
            for i, j in json.load(f).items():
                if i.startswith('SUSE'):
                    project[i] = SUSEUpdate(j)
                elif i.startswith('openSUSE'):
                    project[i] = openSUSEUpdate(j)
                else:
                    raise "Unknown openQA", i
        return project, target, api

    def postoptparse(self):
        # practically quiet
        level = logging.WARNING
        if (self.options.debug):
            level = logging.DEBUG
        elif (self.options.verbose):
            # recomended variant
            level = logging.INFO

        self.logger = logging.getLogger(self.optparser.prog)
        self.logger.setLevel(level)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(levelname)-2s: %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)

        osc.conf.get_config(override_apiurl=self.options.apiurl)

        if (self.options.osc_debug):
            osc.conf.config['debug'] = 1

        self.checker = self.setup_checker()

        if self.options.config:
            self.checker.load_config(self.options.config)

        if self.options.review_mode:
            self.checker.review_mode = self.options.review_mode

        if self.options.fallback_user:
            self.checker.fallback_user = self.options.fallback_user

        if self.options.fallback_group:
            self.checker.fallback_group = self.options.fallback_group

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.force:
            bot.force = True

        bot.do_comments = self.options.comment

        if not self.options.openqa:
            raise osc.oscerr.WrongArgs("missing openqa url")

        bot.openqa = OpenQA_Client(server=self.options.openqa)
        project, target, api = self._load_metadata()
        bot.api_map = api
        bot.tgt_repo = target
        bot.project_settings = project

        return bot
