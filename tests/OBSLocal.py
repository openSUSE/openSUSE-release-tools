import os
from lxml import etree as ET
from osc import conf
from osc.core import get_request
from osc.core import http_GET
from osc.core import makeurl
import subprocess
import unittest

OSCRC = '/tmp/.oscrc-test'
OSCCOOKIEJAR = '/tmp/.osc_cookiejar-test'
APIURL = 'local-test'

class OBSLocalTestCase(unittest.TestCase):
    script = None
    script_apiurl = True
    script_debug = True
    script_debug_osc = True

    def setUp(self):
        if os.path.exists(OSCCOOKIEJAR):
            # Avoid stale cookiejar since local OBS may be completely reset.
            os.remove(OSCCOOKIEJAR)

        self.osc_user('Admin')
        self.apiurl = conf.config['apiurl']
        self.assertOBS()

    def assertOBS(self):
        url = makeurl(self.apiurl, ['about'])
        root = ET.parse(http_GET(url)).getroot()
        self.assertEqual(root.tag, 'about')

    @staticmethod
    def oscrc(userid):
        with open(OSCRC, 'w+') as f:
            f.write('\n'.join([
                '[general]',
                'apiurl = http://api:3000',
                'cookiejar = {}'.format(OSCCOOKIEJAR),
                '[http://api:3000]',
                'user = {}'.format(userid),
                'pass = opensuse',
                'email = {}@example.com'.format(userid),
                'aliases = {}'.format(APIURL),
                '',
            ]))

    def osc_user(self, userid):
        self.oscrc(userid)

        # Rather than modify userid and email, just re-parse entire config and
        # reset authentication by clearing opener to avoid edge-cases.
        self.oscParse()

    def oscParse(self):
        # Otherwise, will stick to first user for a given apiurl.
        conf._build_opener.last_opener = (None, None)

        # Otherwise, will not re-parse same config file.
        if 'cp' in conf.get_configParser.__dict__:
            del conf.get_configParser.cp

        conf.get_config(override_conffile=OSCRC,
                        override_no_keyring=True,
                        override_no_gnome_keyring=True)

    def execute_script(self, args):
        if self.script:
            args.insert(0, self.script)
        if self.script_debug:
            args.insert(1, '--debug')
        if self.script_debug_osc:
            args.insert(1, '--osc-debug')
        args.insert(0, '-p')
        args.insert(0, 'run')
        args.insert(0, 'coverage')

        self.execute(args)

    def execute_osc(self, args):
        # The wrapper allows this to work properly when osc installed via pip.
        args.insert(0, 'osc-wrapper.py')
        self.execute(args)

    def execute(self, args):
        print('$ ' + ' '.join(args)) # Print command for debugging.
        try:
            env = os.environ
            env['OSC_CONFIG'] = OSCRC
            self.output = subprocess.check_output(args, stderr=subprocess.STDOUT, env=env)
        except subprocess.CalledProcessError as e:
            print(e.output)
            raise e
        print(self.output) # For debugging assertion failures.

    def assertOutput(self, string):
        self.assertTrue(string in self.output, '[MISSING] ' + string)

    def assertReview(self, rid, **kwargs):
        request = get_request(self.apiurl, rid)
        for review in request.reviews:
            for key, value in kwargs.items():
                if hasattr(review, key) and getattr(review, key) == value[0]:
                    self.assertEqual(review.state, value[1], '{}={} not {}'.format(key, value[0], value[1]))
                    return

        self.fail('{} not found'.format(kwargs))
