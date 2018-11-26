#!/usr/bin/python3 -u
# Without the -u option for unbuffered output nothing shows up in journal or
# kubernetes logs.

import argparse
from http.cookies import SimpleCookie
from http.cookiejar import Cookie, LWPCookieJar
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import json
import tempfile
import os
from osclib import common
import subprocess
import sys
import time
from urllib.parse import urlparse

# Available in python 3.7.
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    pass

class RequestHandler(BaseHTTPRequestHandler):
    COOKIE_NAME = 'openSUSE_session' # Both OBS and IBS.
    POST_ACTIONS = ['select']

    def do_GET(self):
        if self.path != '/':
            self.send_response(404)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()

        self.write_string('namespace: {}\n'.format(common.NAME))
        self.write_string('name: {}\n'.format('OBS Operator'))
        self.write_string('version: {}\n'.format(common.VERSION))

    def do_POST(self):
        action = self.path.lstrip('/')
        if action not in self.POST_ACTIONS:
            self.send_response(404)
            self.end_headers()
            return

        data = self.data_parse()
        user = data.get('user')
        apiurl = self.apiurl_get()
        if not data or not user or not apiurl:
            self.send_response(400)
            self.end_headers()
            return
        if self.debug:
            print('data: {}'.format(data))
            print('apiurl: {}'.format(apiurl))

        session = self.session_get()
        if not session:
            self.send_response(401)
            self.end_headers()
            return
        if self.debug:
            print('session: {}'.format(session))

        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.send_header('Access-Control-Allow-Credentials', 'true')
        self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin'))
        self.end_headers()

        with tempfile.NamedTemporaryFile() as cookiejar_file:
            with tempfile.NamedTemporaryFile() as oscrc_file:
                self.oscrc_create(oscrc_file, apiurl, cookiejar_file, user)
                self.cookiejar_create(cookiejar_file, session)

                func = getattr(self, 'handle_{}'.format(action))
                commands = func(data)
                for command in commands:
                    self.write_string('$ {}\n'.format(' '.join(command)))
                    if not self.execute(oscrc_file, command):
                        self.write_string('failed')
                        break

    def data_parse(self):
        data = self.rfile.read(int(self.headers['Content-Length']))
        return json.loads(data.decode('utf-8'))

    def apiurl_get(self):
        if self.apiurl:
            return self.apiurl

        origin = self.headers.get('Origin')
        if not origin:
            return None

        # Strip port if present.
        domain = urlparse(origin).netloc.split(':', 2)[0]
        if '.' not in domain:
            return None

        # Remove first subdomain and replace with api subdomain.
        domain_parent = '.'.join(domain.split('.')[1:])
        return 'https://api.{}'.format(domain_parent)

    def session_get(self):
        if self.session:
            return self.session
        else:
            cookie = self.headers.get('Cookie')
            if cookie:
                cookie = SimpleCookie(cookie)
                if self.COOKIE_NAME in cookie:
                    return cookie[self.COOKIE_NAME].value

        return None

    def oscrc_create(self, oscrc_file, apiurl, cookiejar_file, user):
        oscrc_file.write('\n'.join([
            '[general]',
            'apiurl = {}'.format(apiurl),
            'cookiejar = {}'.format(cookiejar_file.name),
            'staging.color = 0',
            '[{}]'.format(apiurl),
            'user = {}'.format(user),
            'pass = invalid',
            '',
        ]).encode('utf-8'))
        oscrc_file.flush()

        # In order to avoid osc clearing the cookie file the modified time of
        # the oscrc file must be set further into the past.
        # if int(round(config_mtime)) > int(os.stat(cookie_file).st_mtime):
        recent_past = time.time() - 3600
        os.utime(oscrc_file.name, (recent_past, recent_past))

    def cookiejar_create(self, cookiejar_file, session):
        cookie_jar = LWPCookieJar(cookiejar_file.name)
        cookie_jar.set_cookie(Cookie(0, self.COOKIE_NAME, session,
            None, False,
            '', False, True,
            '/', True,
            True,
            None, None, None, None, {}))
        cookie_jar.save()
        cookiejar_file.flush()

    def execute(self, oscrc_file, command):
        env = os.environ
        env['OSC_CONFIG'] = oscrc_file.name

        # Would be preferrable to stream incremental output, but python http
        # server does not seem to support this easily.
        result = subprocess.run(command, env=env, stdout=self.wfile, stderr=self.wfile)
        return result.returncode == 0

    def write_string(self, string):
        self.wfile.write(string.encode('utf-8'))

    def staging_command(self, project, subcommand):
        return ['osc', 'staging', '-p', project, subcommand]

    def handle_select(self, data):
        for staging, requests in data['selection'].items():
            command = self.staging_command(data['project'], 'select')
            if 'move' in data and data['move']:
                command.append('--move')
            command.append(staging)
            command.extend(requests)
            yield command

def main(args):
    RequestHandler.apiurl = args.apiurl
    RequestHandler.session = args.session
    RequestHandler.debug = args.debug

    with ThreadedHTTPServer((args.host, args.port), RequestHandler) as httpd:
        print('listening on {}:{}'.format(args.host, args.port))
        httpd.serve_forever()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='OBS Operator server used to perform staging operations.')
    parser.set_defaults(func=main)

    parser.add_argument('--host', default='', help='host name to which to bind')
    parser.add_argument('--port', type=int, default=8080, help='port number to which to bind')
    parser.add_argument('-A', '--apiurl',
        help='OBS instance API URL to use instead of basing from request origin')
    parser.add_argument('--session',
        help='session cookie value to use instead of any passed cookie')
    parser.add_argument('-d', '--debug', action='store_true',
        help='print debugging information')

    args = parser.parse_args()
    sys.exit(args.func(args))
