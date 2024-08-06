from typing import Dict, Optional

from osc import OscConfigParser
from collections import OrderedDict
import os
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
    r'openSUSE:(?P<project>Factory)?$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': '',
        'rings': 'openSUSE:%(project)s:Rings',
        'rebuild': 'openSUSE:%(project)s:Rebuild',
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
        'main-repo': 'standard',
        'pseudometa_package': 'openSUSE:%(project)s:Staging/dashboard',
        'download-baseurl': 'http://download.opensuse.org/tumbleweed/',
        # check_source.py
        'check-source-single-action-require': 'True',
        'devel-project-enforce': 'True',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        'pkglistgen-product-family-include': 'openSUSE:Leap:N',
        'pkglistgen-locales-from': 'openSUSE.product.in',
        'mail-list': 'factory@lists.opensuse.org',
        'mail-maintainer': 'Dominique Leuenberger <dimstar@suse.de>',
        'mail-noreply': 'noreply@opensuse.org',
        'mail-release-list': 'opensuse-releaseteam@opensuse.org',
        'always_set_productversion_to': '',
        'required-source-maintainer': 'group:factory-maintainers',
    },
    r'openSUSE:(?P<project>Factory):ARM$': {
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'main-repo': 'standard',
        'rings': 'openSUSE:%(project)s:ARM:Rings',
        'pseudometa_package': 'openSUSE:%(project)s:ARM:Staging/dashboard',
        'download-baseurl': 'http://download.opensuse.org/ports/aarch64/tumbleweed/',
        'mail-list': 'opensuse-arm@opensuse.org',
        'mail-maintainer': 'Dirk Mueller <dmueller@suse.com>',
        'mail-noreply': 'noreply@opensuse.org',
    },
    r'openSUSE:(?P<project>.*:NonFree)$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'onlyadi': 'True',
        'review-team': 'opensuse-review-team',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+))$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'i586 x86_64',
        'staging-dvd-archs': '',
        'nocleanup-packages': 'bootstrap-copy 000product 000release-packages',
        'rings': 'openSUSE:%(project)s:Rings',
        'rebuild': 'openSUSE:%(project)s:Rebuild',
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
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
        'repo_checker-arch-whitelist': 'x86_64',
        # 16 hour staging window for follow-ups since lower throughput.
        'splitter-staging-age-max': '57600',
        # No special packages since they will pass through SLE first.
        'splitter-special-packages': '',
        'pkglistgen-archs': 'x86_64',
        'pkglistgen-locales-from': 'openSUSE.product',
        'pkglistgen-delete-kiwis-ring1': 'openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi',
        'pkglistgen-delete-kiwis-staging': 'openSUSE-ftp-ftp-x86_64.kiwi openSUSE-cd-mini-x86_64.kiwi',
        'mail-list': 'factory@lists.opensuse.org',
        'mail-maintainer': 'Ludwig Nussel <ludwig.nussel@suse.de>',
        'mail-noreply': 'noreply@opensuse.org',
        'mail-release-list': 'opensuse-releaseteam@opensuse.org',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+)):ARM$': {
        'product': 'openSUSE.product',
        'openqa': 'https://openqa.opensuse.org',
        'main-repo': 'ports',
        'pseudometa_package': 'openSUSE:%(project)s:ARM:Staging/dashboard',
        'pkglistgen-product-family-include': 'openSUSE:Leap:15.0:ARM',
        'download-baseurl-openSUSE-Leap-15.0-ARM': 'http://download.opensuse.org/ports/aarch64/distribution/leap/15.0/',
        'mail-list': 'opensuse-arm@opensuse.org',
        'mail-maintainer': 'Dirk Mueller <dmueller@suse.com>',
        'mail-noreply': 'noreply@opensuse.org',
    },
    r'openSUSE:(?P<project>Leap:(?P<version>[\d.]+)?:Update)$': {
        'main-repo': 'standard',
        'repo_checker-arch-whitelist': 'x86_64',
        'review-install-check': 'maintenance-installcheck',
        'review-openqa': 'qam-openqa',
    },
    r'openSUSE:(?P<project>Backports:(?P<version>[^:]+))$': {
        'staging': 'openSUSE:%(project)s:Staging',
        'staging-group': 'factory-staging',
        'staging-archs': 'x86_64',
        'lock': 'openSUSE:%(project)s:Staging',
        'lock-ns': 'openSUSE',
        'onlyadi': 'True',
        'review-team': 'opensuse-review-team',
        'legal-review-group': 'legal-auto',
        # review-team optionally added by leaper.py.
        'repo_checker-project-skip': 'True',
        # 16 hour staging window for follow-ups since lower throughput.
        'splitter-staging-age-max': '57600',
        # No special packages since they will pass through Leap first.
        'splitter-special-packages': '',
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
        'staging': '',
        'staging-group': '',
        'staging-archs': '',
        'staging-dvd-archs': '',
        'staging-required-checks-adi': '',
        'installcheck-ignore-duplicated-binaries': '',
        'onlyadi': '',
        'nocleanup-packages': '',
        'rings': '',
        'rebuild': '',
        'product': '',
        'openqa': '',
        'lock': '',
        'lock-ns': '',
        '_priority': '0',  # Apply defaults first
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


def str2bool(v: Optional[str]) -> bool:
    return (v is not None and v.lower() in ("yes", "true", "t", "1"))


class Config(object):
    """Helper class for reading the osc configuration file and fetching the
    remote config from the ``OSRT:config`` attribute in the target project.

    """

    def __init__(self, apiurl: str, project: str) -> None:
        self.project = project
        self.remote_values = self.fetch_remote(apiurl)

        conf_file = conf.config.get('conffile', os.environ.get('OSC_CONFIG', '~/.oscrc'))
        self.conf_file = os.path.expanduser(conf_file)

        # Populate the configuration dictionary
        self.populate_conf()

    @staticmethod
    @memoize(session=True)  # Allow reset by memoize_session_reset() for ReviewBot.
    def get(apiurl: str, project: str):
        """Cached version for directly accessing project config."""
        # Properly handle loading the config for interconnect projects.
        from osclib.core import project_remote_apiurl
        apiurl_remote, project_remote = project_remote_apiurl(apiurl, project)

        Config(apiurl_remote, project_remote)
        return conf.config.get(project_remote, [])

    @property
    def conf(self):
        return conf

    def populate_conf(self) -> None:
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

    def fetch_remote(self, apiurl: str) -> Optional[Dict[str, str]]:
        """Fetch the configuration from the ``OSRT`` attribute namespace for the
        current project from the OBS instance with the given apiurl.

        """
        from osclib.core import attribute_value_load
        config = attribute_value_load(apiurl, self.project, 'Config')
        if config:
            cp = OscConfigParser.OscConfigParser()
            cp.read_string(config)
            return dict(cp.items('remote'))

        return None
