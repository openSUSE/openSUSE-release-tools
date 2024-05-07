#!/usr/bin/python3

import sys

import osc.conf
import osc.core
from urllib.error import HTTPError, URLError
import yaml

from osclib.memoize import memoize
from osclib.core import action_is_patchinfo
from osclib.core import owner_fallback
from osclib.core import maintainers_get

import ReviewBot


class MaintenanceChecker(ReviewBot.ReviewBot):
    """ simple bot that adds other reviewers depending on target project
    """

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)
        self.review_messages = {}

    def add_devel_project_review(self, req, package):
        """ add devel project/package as reviewer """
        a = req.actions[0]
        if action_is_patchinfo(a):
            a = req.actions[1]
        project = a.tgt_releaseproject if a.type == 'maintenance_incident' else req.actions[0].tgt_project
        root = owner_fallback(self.apiurl, project, package)

        for p in root.findall('./owner'):
            prj = p.get("project")
            pkg = p.get("package")
            # packages dropped from Factory sometimes point to maintained distros
            if prj.startswith('openSUSE:Leap') or prj.startswith('openSUSE:1'):
                self.logger.debug("%s looks wrong as maintainer, skipped", prj)
                continue
            msg = f'Submission for {pkg} by someone who is not maintainer in the devel project ({prj}). Please review'
            self.add_review(req, by_project=prj, by_package=pkg, msg=msg)

    @staticmethod
    @memoize(session=True)
    def _get_lookup_yml(apiurl, project):
        """ return a dictionary with package -> project mapping
        """
        url = osc.core.makeurl(apiurl, ('source', project, '00Meta', 'lookup.yml'))
        try:
            return yaml.safe_load(osc.core.http_GET(url))
        except (HTTPError, URLError):
            return None

    # check if pkgname was submitted by the correct maintainer. If not, set
    # self.needs_maintainer_review
    def _check_maintainer_review_needed(self, req, a):
        author = req.creator
        if a.type == 'maintenance_incident':
            # check if there is a link and use that or the real package
            # name as src_packge may end with something like
            # .openSUSE_XX.Y_Update
            pkgname = a.src_package
            (linkprj, linkpkg) = self._get_linktarget(a.src_project, pkgname)
            if linkpkg is not None:
                pkgname = linkpkg
            if action_is_patchinfo(a):
                return None

            project = a.tgt_releaseproject
        else:
            pkgname = a.tgt_package
            project = a.tgt_project

        if project.startswith('openSUSE:Leap:') and hasattr(a, 'src_project'):
            mapping = MaintenanceChecker._get_lookup_yml(self.apiurl, project)
            if mapping is None:
                self.logger.error(f"error loading mapping for {project}")
            elif pkgname not in mapping:
                self.logger.debug(f"{pkgname} not tracked")
            else:
                origin = mapping[pkgname]
                self.logger.debug(f"{pkgname} comes from {origin}, submitted from {a.src_project}")
                if origin.startswith('SUSE:SLE-12') and a.src_project.startswith('SUSE:SLE-12') \
                        or origin.startswith('SUSE:SLE-15') and a.src_project.startswith('SUSE:SLE-15') \
                        or origin.startswith('openSUSE:Leap') and a.src_project.startswith('openSUSE:Leap'):
                    self.logger.info(f"{pkgname} submitted from {a.src_project}, no maintainer review needed")
                    return

        maintainers = set(maintainers_get(self.apiurl, project, pkgname))
        if maintainers:
            known_maintainer = False
            for m in maintainers:
                if author == m:
                    self.logger.debug(f"{author} is maintainer")
                    known_maintainer = True
            if not known_maintainer:
                for r in req.reviews:
                    if r.by_user in maintainers:
                        self.logger.debug(f"found {r.by_user} as reviewer")
                        known_maintainer = True
            if not known_maintainer:
                self.logger.debug(f"author: {author}, maintainers: {','.join(maintainers)} => need review")
                self.needs_maintainer_review.add(pkgname)
        else:
            self.logger.warning(f"{pkgname} doesn't have maintainers")
            self.needs_maintainer_review.add(pkgname)

    def check_action_maintenance_incident(self, req, a):

        if a.src_package == 'patchinfo':
            return True

        self._check_maintainer_review_needed(req, a)

        return True

    def check_action_submit(self, req, a):

        self._check_maintainer_review_needed(req, a)

        return True

    def check_action_delete_package(self, req, a):
        self._check_maintainer_review_needed(req, a)

        return True

    def check_one_request(self, req):
        self.needs_maintainer_review = set()

        ret = ReviewBot.ReviewBot.check_one_request(self, req)

        if self.needs_maintainer_review:
            for p in self.needs_maintainer_review:
                self.add_devel_project_review(req, p)

        return ret


if __name__ == "__main__":
    app = ReviewBot.CommandLineInterface()
    app.clazz = MaintenanceChecker
    sys.exit(app.main())
