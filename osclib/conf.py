from __future__ import print_function

from osc import OscConfigParser
from collections import OrderedDict
import io
import os
import operator
import re

from osc import conf
from osclib.memoize import memoize


# Sane defaults for openSUSE and SUSE.  The string interpolation rule
# is as this:
#
# * %(project)s to replace the name of the project.
# * %(project.lower)s to replace the lower case version of the name of
#   the project.

DEFAULT = {
    r'openSUSE:(?P<project>Factory)(?::NonFree)?$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': '',
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
        'pseudometa_package': 'openSUSE:%(project)s:Staging/dashboard',
        'download-baseurl': 'http://download.opensuse.org/tumbleweed/',
        # check_source.py
        'check-source-single-action-require': 'True',
        'devel-project-enforce': 'True',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        'repo-checker': 'repo-checker',
        'repo_checker-no-filter': 'True',
        'repo_checker-package-comment-devel': 'True',
        'pkglistgen-product-family-include': 'openSUSE:Leap:N',
        'mail-list': 'opensuse-factory@opensuse.org',
        'mail-maintainer': 'Dominique Leuenberger <dimstar@suse.de>',
        'mail-noreply': 'noreply@opensuse.org',
        'mail-release-list': 'opensuse-releaseteam@opensuse.org',
    },
    r'openSUSE:(?P<project>Factory):ARM$': {
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'main-repo': 'standard',
        'pseudometa_package': 'openSUSE:%(project)s:ARM:Staging/dashboard',
        'download-baseurl': 'http://download.opensuse.org/ports/aarch64/tumbleweed/',
        'mail-list': 'opensuse-arm@opensuse.org',
        'mail-maintainer': 'Dirk Mueller <dmueller@suse.com>',
        'mail-noreply': 'noreply@opensuse.org',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+))(?::NonFree)?$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': '',
        'nocleanup-packages': 'bootstrap-copy 000product 000release-packages',
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
        'pseudometa_package': 'openSUSE:%(project)s:Staging/dashboard',
        'download-baseurl': 'http://download.opensuse.org/distribution/leap/%(version)s/',
        'download-baseurl-update': 'http://download.opensuse.org/update/leap/%(version)s/',
        'check-source-add-review-team': 'False',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        # check_source.py
        'check-source-single-action-require': 'True',
        # review-team optionally added by leaper.py.
        'repo-checker': 'repo-checker',
        'repo_checker-arch-whitelist': 'x86_64',
        'repo_checker-no-filter': 'True',
        'repo_checker-package-comment-devel': 'True',
        # 16 hour staging window for follow-ups since lower throughput.
        'splitter-staging-age-max': '57600',
        # No special packages since they will pass through SLE first.
        'splitter-special-packages': '',
        # Allow `unselect --cleanup` to operate immediately on:
        # - Update crawler requests (leaper)
        # - F-C-C submitter requests (maxlin_factory)
        'unselect-cleanup-whitelist': 'leaper maxlin_factory',
        'pkglistgen-archs': 'x86_64',
        'pkglistgen-archs-arm': 'aarch64',
        'pkglistgen-archs-ports': 'aarch64 ppc64le',
        'pkglistgen-locales-from': 'openSUSE.product',
        'pkglistgen-include-suggested': 'False',
        'pkglistgen-delete-kiwis-rings': 'openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi',
        'pkglistgen-delete-kiwis-staging': 'openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi',
        'mail-list': 'opensuse-factory@opensuse.org',
        'mail-maintainer': 'Ludwig Nussel <ludwig.nussel@suse.de>',
        'mail-noreply': 'noreply@opensuse.org',
        'mail-release-list': 'opensuse-releaseteam@opensuse.org',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+)):ARM$': {
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'main-repo': 'ports',
        'pseudometa_package': 'openSUSE:%(project)s:ARM:Staging/dashboard',
        'download-baseurl': 'http://download.opensuse.org/ports/aarch64/distribution/leap/%(version)s/',
        'mail-list': 'opensuse-arm@opensuse.org',
        'mail-maintainer': 'Dirk Mueller <dmueller@suse.com>',
        'mail-noreply': 'noreply@opensuse.org',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+)(?::NonFree)?:Update)$': {
        'main-repo': 'standard',
        'leaper-override-group': 'leap-reviewers',
        'repo_checker-arch-whitelist': 'x86_64',
        'repo_checker-no-filter': 'True',
        'repo_checker-package-comment-devel': 'True',
    },
    r'openSUSE:(?P<project>Backports:(?P<version>[^:]+))$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'x86_64',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
        'onlyadi': True,
        'leaper-override-group': 'leap-reviewers',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        # review-team optionally added by leaper.py.
        'repo-checker': 'repo-checker',
        'repo_checker-project-skip': 'True',
        # 16 hour staging window for follow-ups since lower throughput.
        'splitter-staging-age-max': '57600',
        # No special packages since they will pass through Leap first.
        'splitter-special-packages': '',
        # Allow `unselect --cleanup` to operate immediately on:
        # - Update crawler requests (leaper)
        'unselect-cleanup-whitelist': 'leaper',
    },
    r'openSUSE:(?P<project>Backports:SLE-[^:]+(?::Update)?)$': {
        # Skip SLE related projects maintenance projects to avoid processing
        # them during multi-target requests including an openSUSE project. The
        # SLE projects cannot be processed since the repo cannot be mirrored.
        'repo_checker-project-skip': 'True',
        '_priority': '101',
    },
    # Allows devel projects to utilize tools that require config, but not
    # complete StagingAPI support.
    r'(?P<project>.*$)': {
        'staging': None,
        'staging-group': None,
        'staging-archs': '',
        'staging-dvd-archs': '',
        'onlyadi': False,
        'rings': None,
        'nonfree': None,
        'rebuild': None,
        'product': None,
        'openqa': None,
        'lock': None,
        'lock-ns': None,
        'delreq-review': None,
        '_priority': '0', # Apply defaults first
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

    def __init__(self, apiurl, project):
        self.project = project
        self.remote_values = self.fetch_remote(apiurl)

        conf_file = conf.config.get('conffile', os.environ.get('OSC_CONFIG', '~/.oscrc'))
        self.conf_file = os.path.expanduser(conf_file)

        # Populate the configuration dictionary
        self.populate_conf()

    @staticmethod
    @memoize(session=True) # Allow reset by memoize_session_reset() for ReviewBot.
    def get(apiurl, project):
        """Cached version for directly accessing project config."""
        Config(apiurl, project)
        return conf.config.get(project, [])

    @property
    def conf(self):
        return conf

    def populate_conf(self):
        """Add sane default into the configuration and layer (defaults, remote, ~/.oscrc)."""
        defaults = {}
        default_ordered = OrderedDict(sorted(DEFAULT.items(), key=lambda i: int(i[1].get('_priority', 99))))
        for prj_pattern in default_ordered:
            match = re.match(prj_pattern, self.project)
            if match:
                project = match.group('project')
                for k, v in DEFAULT[prj_pattern].items():
                    if k.startswith('_'):
                        continue
                    if isinstance(v, str) and '%(project)s' in v:
                        defaults[k] = v % {'project': project}
                    elif isinstance(v, str) and '%(project.lower)s' in v:
                        defaults[k] = v % {'project.lower': project.lower()}
                    elif isinstance(v, str) and '%(version)s' in v:
                        defaults[k] = v % {'version': match.group('version')}
                    else:
                        defaults[k] = v
                if int(DEFAULT[prj_pattern].get('_priority', 99)) != 0:
                    break

        if self.remote_values:
            defaults.update(self.remote_values)

        # Update the configuration, only when it is necessary
        conf.config[self.project] = self.read_section(self.project, defaults)

    def read_section(self, section, defaults):
        """OSC parser is a bit buggy. Re-read the configuration file to find
        extra sections.

        """
        cp = OscConfigParser.OscConfigParser(defaults=defaults)
        cp.read(self.conf_file)
        if cp.has_section(section):
            return dict(cp.items(section))
        else:
            return defaults

    def fetch_remote(self, apiurl):
        from osclib.core import attribute_value_load
        config = attribute_value_load(apiurl, self.project, 'Config')
        if config:
            cp = OscConfigParser.OscConfigParser()
            config = u'[remote]\n' + config
            cp.readfp(io.StringIO(config))
            return dict(cp.items('remote'))

        return None
