# -*- coding: utf-8 -*-

from update import Update
import re

class openSUSEUpdate(Update):

    repo_prefix = 'http://download.opensuse.org/repositories'
    maintenance_project = 'openSUSE:Maintenance'

    def settings(self, src_prj, dst_prj, packages):
        # strip the architecture for openSUSE - we do them all in one
        dst_prj = re.sub(r':x86_64$', '', dst_prj)
        settings = super(openSUSEUpdate, self).settings(src_prj, dst_prj, packages)
        settings = settings[0]

        # openSUSE:Maintenance key
        settings['IMPORT_GPG_KEYS'] = 'gpg-pubkey-b3fd7e48-5549fd0f'
        settings['ZYPPER_ADD_REPO_PREFIX'] = 'incident'

        if packages:
            # XXX: this may fail in various ways
            # - conflicts between subpackages
            # - added packages
            # - conflicts with installed packages (e.g sendmail vs postfix)
            settings['INSTALL_PACKAGES'] = ' '.join(set([p.name for p in packages]))
            settings['VERIFY_PACKAGE_VERSIONS'] = ' '.join(
                ['{} {}-{}'.format(p.name, p.version, p.release) for p in packages])

        settings['ZYPPER_ADD_REPOS'] = settings['INCIDENT_REPO']
        settings['ADDONURL'] = settings['INCIDENT_REPO']

        settings['WITH_MAIN_REPO'] = 1
        settings['WITH_UPDATE_REPO'] = 1

        return [settings]
