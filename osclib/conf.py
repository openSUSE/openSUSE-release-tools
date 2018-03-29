# Copyright (C) 2015 SUSE Linux GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from ConfigParser import ConfigParser
from collections import OrderedDict
import io
import os
import operator
import re

from osc import conf


# Sane defatuls for openSUSE and SUSE.  The string interpolation rule
# is as this:
#
# * %(project)s to replace the name of the project.
# * %(project.lower)s to replace the lower case version of the name of
#   the project.

DEFAULT = {
    r'openSUSE:(?P<project>Factory)': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': 'x86_64',
        'nocleanup-packages': 'Test-DVD-x86_64 Test-DVD-ppc64le bootstrap-copy',
        'rings': 'openSUSE:%(project)s:Rings',
        'nonfree': 'openSUSE:%(project)s:NonFree',
        'rebuild': 'openSUSE:%(project)s:Rebuild',
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
        'delreq-review': 'factory-maintainers',
        'main-repo': 'standard',
        'download-baseurl': 'http://download.opensuse.org/tumbleweed/',
        # check_source.py
        'devel-project-enforce': 'True',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        'repo-checker': 'repo-checker',
        'pkglistgen-product-family-include': 'openSUSE:Leap:N',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+))': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': 'x86_64',
        'nocleanup-packages': 'Test-DVD-x86_64 bootstrap-copy',
        'rings': 'openSUSE:%(project)s:Rings',
        'nonfree': 'openSUSE:%(project)s:NonFree',
        'rebuild': 'openSUSE:%(project)s:Rebuild',
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
        'leaper-override-group': 'leap-reviewers',
        'delreq-review': None,
        'main-repo': 'standard',
        'download-baseurl': 'http://download.opensuse.org/distribution/leap/%(version)s/',
        'download-baseurl-update': 'http://download.opensuse.org/update/leap/%(version)s/',
        'check-source-add-review-team': 'False',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        # check_source.py
        # review-team optionally added by leaper.py.
        'repo-checker': 'repo-checker',
        'repo_checker-arch-whitelist': 'x86_64',
        # 16 hour staging window for follow-ups since lower throughput.
        'splitter-staging-age-max': '57600',
        # No special packages since they will pass through SLE first.
        'splitter-special-packages': '',
        # Allow `unselect --cleanup` to operate immediately on:
        # - Update crawler requests (leaper)
        # - F-C-C submitter requests (maxlin_factory)
        'unselect-cleanup-whitelist': 'leaper maxlin_factory',
        'pkglistgen-archs': 'x86_64',
        'pkglistgen-archs-ports': 'aarch64',
        'pkglistgen-locales-from': 'openSUSE.product',
        'pkglistgen-include-suggested': '1',
        'pkglistgen-delete-kiwis-rings': 'openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi',
        'pkglistgen-delete-kiwis-staging': 'openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi',
    },
    r'SUSE:(?P<project>SLE-15.*$)': {
        'staging': 'SUSE:%(project)s:Staging',
        'staging-group': 'sle-staging-managers',  # '%(project.lower)s-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': '',
        'rings': 'SUSE:%(project)s:Rings',
        'nonfree': None,
        'rebuild': None,
        'product': None,
        'openqa': None,
        'lock': 'SUSE:%(project)s:Staging',
        'lock-ns': 'SUSE',
        'leaper-override-group': 'sle-release-managers',
        'delreq-review': None,
        'main-repo': 'standard',
        # check_source.py
        'check-source-in-air-rename-allow': 'True',
        'repo-checker': 'repo-checker',
        'repo_checker-package-comment-devel': '',
        'pkglistgen-archs': 'x86_64',
        'pkglistgen-ignore-unresolvable': '1',
        'pkglistgen-ignore-recommended': '1',
        'pkglistgen-product-family-last': 'SUSE:SLE-11:GA',
    },
    r'SUSE:(?P<project>.*$)': {
        'staging': 'SUSE:%(project)s:Staging',
        'staging-group': 'sle-staging-managers',  # '%(project.lower)s-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': '',
        'nocleanup-packages': 'Test-DVD-x86_64 sles-release',
        'rings': None,
        'nonfree': None,
        'rebuild': None,
        'product': None,
        'openqa': None,
        'lock': 'SUSE:%(project)s:Staging',
        'lock-ns': 'SUSE',
        'remote-config': False,
        'delreq-review': None,
        'main-repo': 'standard',
        'priority': '100', # Lower than SLE-15 since less specific.
    },
    # Allows devel projects to utilize tools that require config, but not
    # complete StagingAPI support.
    r'(?P<project>.*$)': {
        'staging': '%(project)s', # Allows for dashboard/config if desired.
        'staging-group': None,
        'staging-archs': '',
        'staging-dvd-archs': '',
        'rings': None,
        'nonfree': None,
        'rebuild': None,
        'product': None,
        'openqa': None,
        'lock': None,
        'lock-ns': None,
        'delreq-review': None,
        'main-repo': 'openSUSE_Factory',
        'remote-config': False,
        'priority': '1000', # Lowest priority as only a fallback.
    },
}

#
# You can overwrite the DEFAULT in the configuration file (~/.oscrc).
# For example, to change the Factory layout you need to add a new
# section like this:
#
# [openSUSE:Factory]
#
# staging = openSUSE:Factory:Staging
# rings = openSUSE:Factory:Rings
# lock = openSUSE:Factory:Staging
#


def str2bool(v):
    return (v is not None and v.lower() in ("yes", "true", "t", "1"))


class Config(object):
    """Helper class to configuration file."""

    def __init__(self, project):
        self.project = project

        conf_file = conf.config.get('conffile', os.environ.get('OSC_CONFIG', '~/.oscrc'))
        self.conf_file = os.path.expanduser(conf_file)
        self.remote_values = None

        # Populate the configuration dictionary
        self.populate_conf()

    @property
    def conf(self):
        return conf

    def populate_conf(self):
        """Add sane default into the configuration."""
        defaults = {}
        default_ordered = OrderedDict(sorted(DEFAULT.items(), key=lambda i: int(i[1].get('priority', 99))))
        for prj_pattern in default_ordered:
            match = re.match(prj_pattern, self.project)
            if match:
                project = match.group('project')
                for k, v in DEFAULT[prj_pattern].items():
                    if isinstance(v, basestring) and '%(project)s' in v:
                        defaults[k] = v % {'project': project}
                    elif isinstance(v, basestring) and '%(project.lower)s' in v:
                        defaults[k] = v % {'project.lower': project.lower()}
                    elif isinstance(v, basestring) and '%(version)s' in v:
                        defaults[k] = v % {'version': match.group('version')}
                    else:
                        defaults[k] = v
                break

        if self.remote_values:
            defaults.update(self.remote_values)

        # Update the configuration, only when it is necessary
        conf.config[self.project] = self.read_section(self.project, defaults)

        # Take the common parameters and check that are there
        params = [set(d) for d in DEFAULT.values()]
        params = reduce(operator.__and__, params)
        if not all(p in conf.config[self.project] for p in params):
            msg = 'Please, add [%s] section in %s, see %s for details' % (self.project, self.conf_file, __file__)
            raise Exception(msg)

    def read_section(self, section, defaults):
        """OSC parser is a bit buggy. Re-read the configuration file to find
        extra sections.

        """
        cp = ConfigParser(defaults=defaults)
        cp.read(self.conf_file)
        if cp.has_section(section):
            return dict(cp.items(section))
        else:
            return defaults

    def apply_remote(self, api):
        """Fetch remote config and re-process (defaults, remote, .oscrc)."""
        if not conf.config[self.project].get('remote-config', True):
            return

        config = api.dashboard_content_load('config')
        if config:
            cp = ConfigParser()
            config = '[remote]\n' + config
            cp.readfp(io.BytesIO(config))
            self.remote_values = dict(cp.items('remote'))
            self.populate_conf()
        elif config is None:
            # Write empty config to allow for caching.
            api.dashboard_content_save('config', '')
