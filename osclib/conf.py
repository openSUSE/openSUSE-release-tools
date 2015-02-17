# Copyright (C) 2015 SUSE Linux Products GmbH
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


DEFAULT = {
    r'openSUSE:(?P<project>[-\w\d]+)': {
        'staging': 'openSUSE:%(project)s:Staging',
        'lock': 'openSUSE:%(project)s:Staging',
    },
    r'SUSE:(?P<project>[-\w\d]+)': {
        'staging': 'SUSE:%(project)s:GA:Staging',
        'lock': 'SUSE:%(project)s:GA:Staging',
    }
}

#
# You can overwrite the DEFAULT in the configuration file (~/.oscrc).
# For example, to change the Factory layout you need to add a new
# section like this:
#
# [openSUSE:Factory]
#
# staging = openSUSE:Factory:Staging
# lock = openSUSE:Factory:Staging
#


class Config(object):
    """Helper class to configuration file."""

    def __init__(self, project):
        self.project = project

        conf_file = os.environ.get('OSC_CONFIG', '~/.oscrc')
        self.conf_file = os.path.expanduser(conf_file)

        # Populate the configuration dictionary
        conf.get_config()
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
                defaults = {
                    k: v % {'project': project} for k, v in DEFAULT[prj_pattern].items() if v
                }
                break

        # Update the configuration, only when it is necessary
        conf.config[self.project] = self.read_section(self.project, defaults)

        # Take the common parameters and check that are there
        params = [set(d) for d in DEFAULT.values()]
        params = reduce(operator.__and__, params)
        if not all(p in conf.config[self.project] for p in params):
            msg = 'Please, add [%s] section in %s' % (self.project, self.conf_file)
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
