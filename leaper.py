#!/usr/bin/python
# Copyright (c) 2014 SUSE Linux Products GmbH
# Copyright (c) 2016 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

from pprint import pprint
import os
import sys
import re
import logging
from optparse import OptionParser
import cmdln

try:
    from xml.etree import cElementTree as ET
except ImportError:
    import cElementTree as ET

import osc.core
from osclib.conf import Config
from osclib.core import devel_project_get
import urllib2
import yaml
import ReviewBot
from check_source_in_factory import FactorySourceChecker

class Leaper(ReviewBot.ReviewBot):

    def __init__(self, *args, **kwargs):
        ReviewBot.ReviewBot.__init__(self, *args, **kwargs)

        # ReviewBot options.
        self.request_default_return = True
        self.comment_handler = True

        self.do_comments = True

        # for FactorySourceChecker
        self.factory = FactorySourceChecker(*args, **kwargs)

        self.needs_legal_review = False
        self.needs_reviewteam = False
        self.pending_factory_submission = False
        self.source_in_factory = None
        self.needs_release_manager = False
        self.release_manager_group = None
        self.review_team_group = None
        self.legal_review_group = None
        self.must_approve_version_updates = False
        self.must_approve_maintenance_updates = False
        self.needs_check_source = False
        self.check_source_group = None
        self.automatic_submission = False

        # project => package list
        self.packages = {}

    def prepare_review(self):
        # update lookup information on every run

        self.lookup.reset()

    def get_source_packages(self, project, expand=False):
        """Return the list of packages in a project."""
        query = {'expand': 1} if expand else {}
        try:
            root = ET.parse(osc.core.http_GET(osc.core.makeurl(self.apiurl, ['source', project],
                                     query=query))).getroot()
            packages = [i.get('name') for i in root.findall('entry')]
        except urllib2.HTTPError as e:
            # in case the project doesn't exist yet (like sle update)
            if e.code != 404:
                raise e
            packages = []

        return packages

    def is_package_in_project(self, project, package):
        if not project in self.packages:
            self.packages[project] = self.get_source_packages(project)
        return True if package in self.packages[project] else False

    def rdiff_link(self, src_project, src_package, src_rev, target_project, target_package = None):
        if target_package is None:
            target_package = src_package

        return '[%(target_project)s/%(target_package)s](/package/show/%(target_project)s/%(target_package)s) ([diff](/package/rdiff/%(src_project)s/%(src_package)s?opackage=%(target_package)s&oproject=%(target_project)s&rev=%(src_rev)s))'%{
                'src_project': src_project,
                'src_package': src_package,
                'src_rev': src_rev,
                'target_project': target_project,
                'target_package': target_package,
                }

    def _check_same_origin(self, origin, project):

        if origin == 'FORK':
            return True

        if origin.startswith('Devel;'):
            (dummy, origin, dummy) = origin.split(';')

        # FIXME: to make the rest of the code easier this should probably check
        # if the srcmd5 matches the origin project. That way it doesn't really
        # matter from where something got submitted as long as the sources match.
        return project.startswith(origin)

    def check_source_submission(self, src_project, src_package, src_rev, target_project, target_package):
        ret = self.check_source_submission_inner(src_project, src_package, src_rev, target_project, target_package)

        # The layout of this code is just plain wrong and awkward. What is
        # really desired a "post check source submission" not
        # check_one_request() which applies to all action types and all actions
        # at once. The follow-ups need the same context that the main method
        # determining to flip the switch had. For maintenance incidents the
        # ReviewBot code does a fair bit of mangling to the package and project
        # values passed in which is not present during check_one_request().
        # Currently the only feature used by maintenance is
        # do_check_maintainer_review and the rest of the checks apply in the
        # single action workflow so this should work fine, but really all the
        # processing should be done per-action instead of per-request.

        if self.do_check_maintainer_review:
            self.devel_project_review_ensure(self.request, target_project, target_package)

        return ret

    def check_source_submission_inner(self, src_project, src_package, src_rev, target_project, target_package):
        super(Leaper, self).check_source_submission(src_project, src_package, src_rev, target_project, target_package)
        self.automatic_submission = False

        if src_project == target_project and src_package == target_package:
            self.logger.info('self submission detected')
            self.needs_release_manager = True
            return True

        src_srcinfo = self.get_sourceinfo(src_project, src_package, src_rev)
        package = target_package

        origin = self.lookup.get(target_project, package)
        origin_same = True
        if origin:
            origin_same = self._check_same_origin(origin, src_project)

        if src_srcinfo is None:
            # source package does not exist?
            # handle here to avoid crashing on the next line
            self.logger.warn("Could not get source info for %s/%s@%s" % (src_project, src_package, src_rev))
            return False

        if self.ibs and target_project.startswith('SUSE:SLE'):

            review_result = None
            prj = 'openSUSE.org:openSUSE:Factory'
            if self.is_package_in_project(prj, package):
                # True or None (open request) are acceptable for SLE.
                in_factory = self._check_factory(package, src_srcinfo, prj)
                if in_factory:
                    review_result = True
                    self.source_in_factory = True
                elif in_factory is None:
                    self.pending_factory_submission = True
                else:
                    self.logger.info('different sources in {}'.format(self.rdiff_link(src_project, src_package, src_rev, prj, package)))
            else:
                self.logger.info('the package is not in Factory, nor submitted there')

            if review_result == None:
                other_projects_to_check = []
                m = re.match('SUSE:SLE-(\d+)(?:-SP(\d+)):', target_project)
                if m:
                    sle_version = int(m.group(1))
                    sp_version = int(m.group(2))
                    versions_to_check = []
                    # yeah, too much harcoding here
                    if sle_version == 12:
                        versions_to_check = [ '42.3' ]
                    elif sle_version == 15:
                        versions_to_check = [ '15.%d'%i for i in range(sp_version+1) ]
                    else:
                        self.logger.error("can't handle %d.%d", sle_version, sp_version)

                    for version in versions_to_check:
                        leap = 'openSUSE.org:openSUSE:Leap:%s'%(version)
                        other_projects_to_check += [ leap, leap + ':Update', leap + ':NonFree', leap + ':NonFree:Update' ]

                for prj in other_projects_to_check:
                    if self.is_package_in_project(prj, package):
                        self.logger.info('checking {}'.format(prj))
                        if self._check_factory(package, src_srcinfo, prj) is True:
                            self.logger.info('found source match in {}'.format(prj))
                        else:
                            self.logger.info('different sources in {}'.format(self.rdiff_link(src_project, src_package, src_rev, prj, package)))

                devel_project, devel_package = devel_project_get(self.apiurl, 'openSUSE.org:openSUSE:Factory', package)
                if devel_project is not None:
                    # specifying devel package is optional
                    if devel_package is None:
                        devel_package = package
                    if self.is_package_in_project(devel_project, devel_package):
                        if self._check_matching_srcmd5(devel_project, devel_package, src_srcinfo.verifymd5) == True:
                            self.logger.info('matching sources in {}/{}'.format(devel_project, devel_package))
                            return True
                        else:
                            self.logger.info('different sources in devel project {}'.format(self.rdiff_link(src_project, src_package, src_rev, devel_project, devel_package)))
                else:
                    self.logger.info('no devel project found for {}/{}'.format('openSUSE.org:openSUSE:Factory', package))

                self.logger.info('no matching sources found anywhere. Needs a human to decide whether that is ok. Please provide some justification to help that person.')

            if not review_result:
                review_result = origin_same
                if origin_same:
                    self.logger.info("ok, origin %s unchanged", origin)
                else:
                    # only log origin state if it's taken into consideration for the review result
                    self.logger.info("Submitted from a different origin than expected ('%s')", origin)

            if not review_result and self.override_allow:
                # Rather than decline, leave review open in-case of change and
                # ask release manager for input via override comment.
                self.logger.info('Comment `(at){} override accept` to force accept.'.format(self.review_user))
                self.needs_release_manager = True
                review_result = None

            return review_result

        if target_project.endswith(':Update'):
            self.logger.info("expected origin is '%s' (%s)", origin,
                             "unchanged" if origin_same else "changed")

            # Only when not from current product should request require maintainer review.
            self.do_check_maintainer_review = False

            if origin_same:
                return True

            good = self._check_matching_srcmd5(origin, target_package, src_srcinfo.verifymd5)
            if good:
                self.logger.info('submission source found in origin ({})'.format(origin))
                return good
            good = self.factory._check_requests(origin, target_package, src_srcinfo.verifymd5)
            if good or good == None:
                self.logger.info('found pending submission against origin ({})'.format(origin))
                return good

            self.do_check_maintainer_review = True

            return None
        elif self.action.type == 'maintenance_incident':
            self.logger.debug('unhandled incident pattern (targetting non :Update project)')
            return True

        # obviously
        if src_project in ('openSUSE:Factory', 'openSUSE:Factory:NonFree'):
            self.source_in_factory = True

        is_fine_if_factory = False
        not_in_factory_okish = False
        if origin:
            self.logger.info("expected origin is '%s' (%s)", origin,
                    "unchanged" if origin_same else "changed")
            if origin.startswith('Devel;'):
                if origin_same == False:
                    self.logger.debug("not submitted from devel project")
                    return False
                is_fine_if_factory = True
                not_in_factory_okish = True
                if self.must_approve_version_updates:
                    self.needs_release_manager = True
                # fall through to check history and requests
            elif origin.startswith('openSUSE:Factory'):
                # A large number of requests are created by hand that leaper
                # would have created via update_crawler.py. This applies to
                # other origins, but primary looking to let Factory submitters
                # know that there is no need to make manual submissions to both.
                # Since it has a lookup entry it is not a new package.
                self.automatic_submission = False
                if self.must_approve_version_updates:
                    self.needs_release_manager = True
                if origin == src_project:
                    self.source_in_factory = True
                    # no need to approve submissions from Factory if
                    # the lookup file points to Factory. Just causes
                    # spam for many maintainers #1393
                    self.do_check_maintainer_review = False
                is_fine_if_factory = True
                # fall through to check history and requests
            elif origin == 'FORK':
                is_fine_if_factory = True
                if not src_project.startswith('SUSE:SLE-'):
                    not_in_factory_okish = True
                    self.needs_check_source = True
                self.needs_release_manager = True
                # fall through to check history and requests
            # TODO Ugly save for 15.1 (n-1).
            elif origin.startswith('openSUSE:Leap:15.0'):
                if self.must_approve_maintenance_updates:
                    self.needs_release_manager = True
                # submitted from :Update
                if origin_same:
                    self.logger.debug("submission from 15.0 ok")
                    return True
                # switching to sle package might make sense
                if src_project.startswith('SUSE:SLE-15'):
                    self.needs_release_manager = True
                    return True
                # submitted from elsewhere but is in :Update
                else:
                    good = self._check_matching_srcmd5('openSUSE:Leap:15.0:Update', target_package, src_srcinfo.verifymd5)
                    if good:
                        self.logger.info("submission found in 15.0")
                        return good
                    # check release requests too
                    good = self.factory._check_requests('openSUSE:Leap:15.0:Update', target_package, src_srcinfo.verifymd5)
                    if good or good == None:
                        self.logger.debug("found request")
                        return good
                # let's see where it came from before
                oldorigin = self.lookup.get('openSUSE:Leap:15.0', target_package)
                if oldorigin:
                    self.logger.debug("oldorigin {}".format(oldorigin))
                    # Factory. So it's ok to keep upgrading it to Factory
                    # TODO: whitelist packages where this is ok and block others?
                    self.logger.info("Package was from %s in 15.0", oldorigin)
                    if oldorigin.startswith('openSUSE:Factory'):
                        # check if an attempt to switch to SLE package is made
                        for sp in ('SP1:GA', 'SP1:Update'):
                            good = self._check_matching_srcmd5('SUSE:SLE-15-{}'.format(sp), target_package, src_srcinfo.verifymd5)
                            if good:
                                self.logger.info("request sources come from SLE")
                                self.needs_release_manager = True
                                return good
                    # TODO Ugly save for 15.2 (n-2).
                    elif False and oldorigin.startswith('openSUSE:Leap:15.0'):
                        self.logger.info("Package was from %s in 15.0", oldorigin)
                # the release manager needs to review attempts to upgrade to Factory
                is_fine_if_factory = True
                self.needs_release_manager = True

            elif origin.startswith('SUSE:SLE-15'):
                if self.must_approve_maintenance_updates:
                    self.needs_release_manager = True
                for v in ('15.0', '15.1'):
                    prj = 'openSUSE:Leap:{}:SLE-workarounds'.format(v)
                    if self.is_package_in_project( prj, target_package):
                        self.logger.info("found package in %s", prj)
                        if not self._check_matching_srcmd5(prj,
                                target_package,
                                src_srcinfo.verifymd5):
                            self.logger.info("sources in %s are NOT identical",
                                    self.rdiff_link(src_project, src_package, src_rev, prj, package))

                        self.needs_release_manager = True
                # submitted from :Update
                if origin == src_project:
                    self.logger.debug("submission origin ok")
                    return True
                elif origin.endswith(':GA') \
                    and src_project == origin[:-2]+'Update':
                    self.logger.debug("sle update submission")
                    return True

                # check  if submitted from higher SP
                priolist = ['SUSE:SLE-15:', 'SUSE:SLE-15-SP1:', 'SUSE:SLE-15-SP2:', 'SUSE:SLE-15-SP3:']
                for i in range(len(priolist)-1):
                    if origin.startswith(priolist[i]):
                        for prj in priolist[i+1:]:
                            if src_project.startswith(prj):
                                self.logger.info("submission from higher service pack %s:* ok", prj)
                                return True

                in_sle_origin = self._check_factory(target_package, src_srcinfo, origin)
                if in_sle_origin:
                    self.logger.info('parallel submission, also in {}'.format(origin))
                    return True

                self.needs_release_manager = True
                # the release manager needs to review attempts to upgrade to Factory
                is_fine_if_factory = True
            else:
                self.logger.error("unhandled origin %s", origin)
                return False
        else: # no origin
            # submission from SLE is ok
            if src_project.startswith('SUSE:SLE-15'):
                self.do_check_maintainer_review = False
                return True

            # new package submitted from Factory. Check if it was in
            # 42.3 before and skip maintainer review if so.
            subprj = src_project[len('openSUSE:Factory'):]
            # disabled for reference. Needed again for 16.0 probably
            if False and self.source_in_factory and target_project.startswith('openSUSE:Leap:15.0') \
                and self.is_package_in_project('openSUSE:Leap:42.3'+subprj, package):
                    self.logger.info('package was in 42.3')
                    self.do_check_maintainer_review = False
                    return True

            is_fine_if_factory = True
            self.needs_release_manager = True

        if origin is None or not origin.startswith('SUSE:SLE-'):
            for p in (':Update', ':GA'):
                prj = 'SUSE:SLE-15' + p
                if self.is_package_in_project(prj, package):
                    self.logger.info('Package is in {}'.format(
                        self.rdiff_link(src_project, src_package, src_rev, prj, package)))
                    break

        is_in_factory = self.source_in_factory

        # we came here because none of the above checks find it good, so
        # let's see if the package is in Factory at least
        if is_in_factory is None:
            is_in_factory = self._check_factory(package, src_srcinfo)
        if is_in_factory:
            self.source_in_factory = True
            self.needs_reviewteam = False
            self.needs_legal_review = False
        elif is_in_factory is None:
            self.pending_factory_submission = True
            self.needs_reviewteam = False
            self.needs_legal_review = False
        else:
            if src_project.startswith('SUSE:SLE-15') \
                or src_project.startswith('openSUSE:Leap:15.'):
                self.needs_reviewteam = False
                self.needs_legal_review = False
            else:
                self.needs_reviewteam = True
                self.needs_legal_review = True
            self.source_in_factory = False

        if is_fine_if_factory:
            if self.source_in_factory:
                return True
            elif self.pending_factory_submission:
                return None
            elif not_in_factory_okish:
                self.needs_reviewteam = True
                self.needs_legal_review = True
                return True

        if self.override_allow:
            # Rather than decline, leave review open and ask release
            # manager for input via override comment.
            self.logger.info('Comment `(at){} override accept` to force accept.'.format(self.review_user))
            self.needs_release_manager = True
            return None

        return False

    def _check_factory(self, target_package, src_srcinfo, target_project='openSUSE:Factory'):
        for subprj in ('', ':NonFree', ':Live'):
            prj = ''.join((target_project, subprj))
            good = self._check_matching_srcmd5(prj, target_package, src_srcinfo.verifymd5)
            if good:
                return good
            good = self.factory._check_requests(prj, target_package, src_srcinfo.verifymd5)
            if good or good == None:
                self.logger.debug("found request to %s", prj)
                return good

        return False

    def _check_project_and_request(self, project, target_package, src_srcinfo):
        good = self._check_matching_srcmd5(project, target_package, src_srcinfo.verifymd5)
        if good:
            return good
        good = self.factory._check_requests(project, target_package, src_srcinfo.verifymd5)
        if good or good == None:
            return good
        return False

    def check_one_request(self, req):
        config = Config.get(self.apiurl, req.actions[0].tgt_project)
        self.needs_legal_review = False
        self.needs_reviewteam = False
        self.needs_release_manager = False
        self.pending_factory_submission = False
        self.source_in_factory = None
        self.do_check_maintainer_review = not self.ibs
        self.packages = {}

        request_ok = ReviewBot.ReviewBot.check_one_request(self, req)

        self.logger.debug("review result: %s", request_ok)
        if self.pending_factory_submission:
            self.logger.info("submission is waiting for a Factory request to complete")
            creator = req.get_creator()
            bot_name = self.bot_name.lower()
            if self.automatic_submission and creator != bot_name:
                self.logger.info('@{}: this request would have been automatically created by {} after the Factory submission was accepted in order to eleviate the need to manually create requests for packages sourced from Factory'.format(creator, bot_name))
        elif self.source_in_factory:
            self.logger.info("perfect. the submitted sources are in or accepted for Factory")
        elif self.source_in_factory == False:
            self.logger.warn("the submitted sources are NOT in Factory")

        if request_ok == False:
            self.logger.info("NOTE: if you think the automated review was wrong here, please talk to the release team before reopening the request")

        if self.do_comments:
            result = None
            if request_ok is None:
                state = 'seen'
            elif request_ok:
                state = 'done'
                result = 'accepted'
            else:
                state = 'done'
                result = 'declined'

            self.comment_write(state, result)

        add_review_groups = []
        if self.needs_release_manager:
            add_review_groups.append(self.release_manager_group or
                                     config.get(self.override_group_key))
        if self.needs_reviewteam:
            add_review_groups.append(self.review_team_group or
                                     config.get('review-team'))
        if self.needs_legal_review:
            add_review_groups.append(self.legal_review_group or
                                     config.get('legal-review-group'))
        if self.needs_check_source and self.check_source_group is not None:
            add_review_groups.append(self.check_source_group)

        for group in add_review_groups:
            if group is None:
                continue
            self.logger.info("{0} needs review by [{1}](/group/show/{1})".format(req.reqid, group))
            self.add_review(req, by_group=group)

        return request_ok

    def check_action__default(self, req, a):
        self.needs_release_manager = True
        return super(Leaper, self).check_action__default(req, a)

class CommandLineInterface(ReviewBot.CommandLineInterface):

    def __init__(self, *args, **kwargs):
        ReviewBot.CommandLineInterface.__init__(self, args, kwargs)
        self.clazz = Leaper

    def get_optparser(self):
        parser = ReviewBot.CommandLineInterface.get_optparser(self)

        parser.add_option("--no-comment", dest='comment', action="store_false", default=True, help="don't actually post comments to obs")
        parser.add_option("--manual-version-updates", action="store_true", help="release manager must approve version updates")
        parser.add_option("--manual-maintenance-updates", action="store_true", help="release manager must approve maintenance updates")
        parser.add_option("--check-source-group", dest="check_source_group", metavar="GROUP", help="group used by check_source.py bot which will be added as a reviewer should leaper checks pass")
        parser.add_option("--review-team-group", dest="review_team_group", metavar="GROUP", help="group used for package reviews")
        parser.add_option("--release-manager-group", dest="release_manager_group", metavar="GROUP", help="group used for release manager reviews")
        parser.add_option("--legal-review-group", dest="legal_review_group", metavar="GROUP", help="group used for legal reviews")

        return parser

    def setup_checker(self):
        bot = ReviewBot.CommandLineInterface.setup_checker(self)

        if self.options.manual_version_updates:
            bot.must_approve_version_updates = True
        if self.options.manual_maintenance_updates:
            bot.must_approve_maintenance_updates = True
        if self.options.check_source_group:
            bot.check_source_group = self.options.check_source_group
        if self.options.review_team_group:
            bot.review_team_group = self.options.review_team_group
        if self.options.legal_review_group:
            bot.legal_review_group = self.options.legal_review_group
        if self.options.release_manager_group:
            bot.release_manager_group = self.options.release_manager_group
        bot.do_comments = self.options.comment

        return bot

if __name__ == "__main__":
    app = CommandLineInterface()
    sys.exit( app.main() )

