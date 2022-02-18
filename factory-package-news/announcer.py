#!/usr/bin/python3

import http.client
import re
from urllib.parse import urlparse, urljoin
import smtplib
from email.mime.text import MIMEText
import sys
import email.utils
import argparse
import logging
import yaml

logger = logging.getLogger()

# map of default config entries
config_defaults = {
    'sender': 'noreply@opensuse.org',
    'to': 'factory@lists.opensuse.org',
    'relay': 'relay.suse.de',
    'changesfile': "Changes.{version}.txt",
}


def _load_config(handle=None):
    d = config_defaults
    y = yaml.safe_load(handle) if handle is not None else {}
    keys = set(d.keys()) | set(y.keys())
    for key in keys:
        y[key] = y.get(key, d.get(key, None))
    return y


parser = argparse.ArgumentParser(description="Announce new snapshots")
parser.add_argument("--dry", action="store_true", help="dry run")
parser.add_argument("--debug", action="store_true", help="debug output")
parser.add_argument("--verbose", action="store_true", help="verbose")
parser.add_argument("--from", dest='sender', metavar="EMAIL", help="sender email address")
parser.add_argument("--to", metavar="EMAIL", help="recepient email address")
parser.add_argument("--relay", metavar="RELAY", help="SMTP relay server address")
parser.add_argument("--version", metavar="VERSION", help="announce specific version")
parser.add_argument("--config", metavar="FILE", type=argparse.FileType(), help="YAML config file to override defaults")
parser.add_argument("--dump-config", action="store_true", help="dump built in YAML config")
parser.add_argument("--state-file", metavar="STATE_FILE", help="Yaml config of previously announced", required=True)
options = parser.parse_args()

# Set logging configuration
logging.basicConfig(level=logging.DEBUG if options.debug
                    else logging.INFO,
                    format='%(asctime)s - %(module)s:%(lineno)d - %(levelname)s - %(message)s')

state = {}
try:
    with open(options.state_file, 'r') as file:
        state = yaml.safe_load(file)
        if state is None:
            state = {}
except IOError:
    pass

config = _load_config(options.config)

if options.sender:
    config['sender'] = options.sender
if options.to:
    config['to'] = options.to
if options.relay:
    config['relay'] = options.relay

if options.dump_config:
    print(yaml.dump(config, default_flow_style=False))
    sys.exit(0)

if not config['sender'] or not config['to'] or not config['relay']:
    logger.error("need to specify --from and --to and --relay")
    sys.exit(1)

if not options.version:
    u = urlparse(urljoin(config['url'], config['iso']))
    conn = http.client.HTTPConnection(u.hostname, 80)
    conn.request('HEAD', u.path)
    res = conn.getresponse()
    if res.status != 302:
        raise Exception("http fail: %s %s" % (res.status, res.reason))

    loc = res.getheader('location')
    if loc is None:
        raise Exception("empty location!")

    m = re.search(r'(?:Snapshot|Build)([\d.]+)-Media', loc)
    if m is None:
        raise Exception("failed to parse %s" % loc)

    version = m.group(1)
    logger.debug("found version %s", version)
else:
    version = options.version

if state.get(config['name'], None) == version:
    logger.info("version unchanged, exit")
    sys.exit(0)

url = urljoin(config['url'], config['changesfile'].format(version=version))
# take the safer route
url = url.replace('download.opensuse.org', 'downloadcontent.opensuse.org')
u = urlparse(url)
conn = http.client.HTTPConnection(u.hostname, 80)
conn.request('HEAD', u.path)
res = conn.getresponse()
if res.status == 302:
    loc = res.getheader('location')
    if loc is None:
        raise Exception("empty location!")
    u = urlparse(loc)

conn = http.client.HTTPConnection(u.hostname, 80)
conn.request('GET', u.path)
res = conn.getresponse()
if res.status != 200:
    raise Exception("http %s fail: %s %s" % (u, res.status, res.reason))

txt = res.read().decode('latin1')
if '====' not in txt:
    logger.error("no changes or file corrupt? not sending anything")
    sys.exit(1)

msg = MIMEText(config['bodytemplate'].format(version=version, text=txt))
msg['Subject'] = config['subject'].format(version=version)
msg['From'] = config['sender']
msg['To'] = config['to']
msg['Mail-Followup-To'] = config['to']
msg['Date'] = email.utils.formatdate(localtime=1)
msg['Message-ID'] = email.utils.make_msgid()

if options.dry:
    print("sending ...")
    print(msg.as_string())
else:
    logger.info("announcing version {}".format(version))
    s = smtplib.SMTP(config['relay'])
    s.send_message(msg)
    s.quit()

state[config['name']] = version
with open(options.state_file, 'w') as file:
    yaml.dump(state, file)
