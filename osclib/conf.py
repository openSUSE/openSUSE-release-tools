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
        'staging-archs': 'i586 x86_64 ppc64le',
        'staging-dvd-archs': 'x86_64 ppc64le',
        'nocleanup-packages': 'Test-DVD-x86_64 Test-DVD-ppc64le bootstrap-copy',
        'rings': 'openSUSE:%(project)s:Rings',
        'nonfree': 'openSUSE:%(project)s:NonFree',
        'rebuild': 'openSUSE:%(project)s:Rebuild',
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
    },
    r'openSUSE:(?P<project>Leap:[\d.]+)': {
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
    },
    r'SUSE:(?P<project>.*$)': {
        'staging': 'SUSE:%(project)s:Staging',
        'staging-group': 'sle-staging-managers',  # '%(project.lower)s-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': 'x86_64',
        'nocleanup-packages': 'Test-DVD-x86_64 sles-release',
        'rings': None,
        'nonfree': None,
        'rebuild': None,
        'product': None,
        'openqa': None,
        'lock': 'SUSE:%(project)s:Staging',
        'lock-ns': 'SUSE',
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


class Config(object):
    """Helper class to configuration file."""

    def __init__(self, project):
        self.project = project

        conf_file = os.environ.get('OSC_CONFIG', '~/.oscrc')
        self.conf_file = os.path.expanduser(conf_file)

        # Populate the configuration dictionary
        self.populate_conf()

    @property
    def conf(self):
        return conf

    def populate_conf(self):
        """Add sane default into the configuration."""
        defaults = {}
        for prj_pattern in DEFAULT:
            match = re.match(prj_pattern, self.project)
            if match:
                project = match.group('project')
                for k, v in DEFAULT[prj_pattern].items():
                    if isinstance(v, basestring) and '%(project)s' in v:
                        defaults[k] = v % {'project': project}
                    elif isinstance(v, basestring) and '%(project.lower)s' in v:
                        defaults[k] = v % {'project.lower': project.lower()}
                    else:
                        defaults[k] = v
                break

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
