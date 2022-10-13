from io import StringIO
from datetime import datetime
from typing import List
import dateutil.parser
import logging
import textwrap
from urllib.error import HTTPError, URLError

import time
import re
from lxml import etree as ET


from osc import conf
from osc import oscerr
from osclib.core import attribute_value_load
from osclib.core import attribute_value_save
from osc.core import show_package_meta
from osc.core import buildlog_strip_time
from osc.core import change_review_state
from osc.core import delete_project
from osc.core import get_commitlog
from osc.core import get_group
from osc.core import get_request
from osc.core import make_meta_url
from osc.core import makeurl
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT
from osc.core import http_DELETE
from osc.core import rebuild
from osc.core import search
from osc.core import show_project_meta
from osc.core import show_project_sourceinfo
from osc.core import streamfile
from osc.util.helper import decode_it

from osclib.cache import Cache
from osclib.core import devel_project_get
from osclib.core import entity_exists
from osclib.core import project_pseudometa_file_load
from osclib.core import project_pseudometa_file_save
from osclib.core import project_pseudometa_file_ensure
from osclib.core import source_file_load
from osclib.comments import CommentAPI
from osclib.ignore_command import IgnoreCommand
from osclib.memoize import memoize
from osclib.freeze_command import MAX_FROZEN_AGE


class StagingAPI(object):
    """
    Class containing various api calls to work with staging projects.
    """

    def __init__(self, apiurl: str, project: str):
        """Initialize instance variables."""

        self.apiurl = apiurl
        self.project = project

        # Store some prefix / data used in the code.
        self.user = conf.get_apiurl_usr(apiurl)
        self._rings = None
        self._ring_packages = None
        self._ring_packages_for_links = None
        self._packages_staged = None
        self._package_metas = dict()
        self._supersede = False
        self._package_disabled = {}
        self._is_staging_manager = None

        Cache.init()

    def __getattr__(self, attr):
        """Lazy-load all config values to allow for placement in remote config."""
        if attr.startswith('c'):
            # Drop 'c' prefix and change to config key format.
            key = attr[1:].replace('_', '-')

            # This will intentionally cause error if key does not exists.
            value = conf.config[self.project][key]
            if key.endswith('archs') or key == 'nocleanup-packages':
                value = value.split()

            # This code will only be called for the first access.
            setattr(self, attr, value)
            return value

        # Raise AttributeError like normal.
        return self.__getattribute__(attr)

    @property
    def rings(self):
        if self._rings is None:

            # If the project support rings, inititialize some variables.
            if self.crings:
                self._rings = (
                    '{}:0-Bootstrap'.format(self.crings),
                    '{}:1-MinimalX'.format(self.crings)
                )
            else:
                self._rings = []

        return self._rings

    @property
    def ring_packages(self):
        if self._ring_packages is None:
            self._ring_packages = self._generate_ring_packages()

        return self._ring_packages

    @ring_packages.setter
    def ring_packages(self, value):
        raise Exception("setting ring_packages is not allowed")

    @property
    def ring_packages_for_links(self):
        if self._ring_packages_for_links is None:
            self._ring_packages_for_links = self._generate_ring_packages(checklinks=True)

        return self._ring_packages_for_links

    @ring_packages_for_links.setter
    def ring_packages_for_links(self, value):
        raise Exception("setting ring_packages_path is not allowed")

    @property
    def packages_staged(self):
        if self._packages_staged is None:
            self._packages_staged = self._get_staged_requests()

        return self._packages_staged

    @packages_staged.setter
    def packages_staged(self, value):
        raise Exception("setting packages_staged is not allowed")

    @property
    def is_staging_manager(self):
        if self._is_staging_manager is None:
            self._is_staging_manager = self.is_user_member_of(self.user, self.cstaging_group)
        return self._is_staging_manager

    def project_has_repo(self, repo_name):
        # Determine if the project has a repo with given name
        meta = self.get_prj_meta(self.project)
        xpath = f'repository[@name="{repo_name}"]'
        return len(meta.xpath(xpath)) > 0

    def makeurl(self, paths: List[str], query=None) -> str:
        """
        Wrapper around osc's makeurl passing our apiurl
        :return url made for l and query
        """
        query = [] if not query else query
        return makeurl(self.apiurl, paths, query)

    def _retried_request(self, url, func, data=None):
        retry_sleep_seconds = 1
        while True:
            try:
                if data is not None:
                    return func(url, data=data)
                return func(url)
            except HTTPError as e:
                if 500 <= e.code <= 599:
                    print('Error {}, retrying {} in {}s'.format(e.code, url, retry_sleep_seconds))
                elif e.code == 400 and e.reason == 'service in progress':
                    print('Service in progress, retrying {} in {}s'.format(url, retry_sleep_seconds))
                else:
                    raise e
                time.sleep(retry_sleep_seconds)
                # increase sleep time up to one minute to avoid hammering
                # the server in case of real problems
                if (retry_sleep_seconds % 60):
                    retry_sleep_seconds += 1

    def retried_GET(self, url):
        return self._retried_request(url, http_GET)

    def retried_POST(self, url, data=None):
        return self._retried_request(url, http_POST, data)

    def retried_PUT(self, url, data):
        return self._retried_request(url, http_PUT, data)

    def _generate_ring_packages(self, checklinks=False):
        """
        Generate dictionary with names of the rings
        :param checklinks: return dictionary with ring names and the proper ring path for list only
        :return dictionary with ring names
        """

        ret = {}
        # puts except packages and it's origin project path
        except_pkgs = {}

        for prj in self.rings:
            query = {
                'view': 'info',
                'nofilename': '1'
            }

            url = self.makeurl(['source', prj], query)
            root = http_GET(url)

            for si in ET.parse(root).getroot().findall('sourceinfo'):
                pkg = si.get('package')
                if ':' in pkg:
                    continue
                if pkg in ret:
                    msg = '{} is defined in two projects ({} and {})'
                    filelist = self.get_filelist_for_package(pkgname=pkg, project=prj, expand='1')
                    if '_multibuild' in filelist:
                        logging.debug(msg.format(pkg, ret[pkg], prj))
                        msg = ''
                    if pkg.startswith('000') or (checklinks and pkg in except_pkgs and prj == except_pkgs[pkg]):
                        msg = ''
                    if len(msg):
                        raise Exception(msg.format(pkg, ret[pkg], prj))
                if pkg not in ret:
                    ret[pkg] = prj

                # put the ring1 package to ring0 list if it was linked from ring0 subpacakge
                if checklinks:
                    if not prj.endswith('0-Bootstrap'):
                        continue
                    for linked in si.findall('linked'):
                        linked_prj = linked.get('project')
                        linked_pkg = linked.get('package')
                        if linked_prj != self.project and pkg != linked_pkg:
                            if linked_pkg not in ret:
                                except_pkgs[linked_pkg] = linked_prj
                                ret[linked_pkg] = prj
        return ret

    def _get_staged_requests(self):
        """
        Get all requests that are already staged
        :return dict of staged requests with their project and srid
        """

        packages_staged = {}
        url = self.makeurl(['staging', self.project, 'staging_projects'], {'requests': 1})
        status = ET.parse(self.retried_GET(url)).getroot()
        for prj in status.findall('staging_project'):
            for req in prj.findall('./staged_requests/request'):
                packages_staged[req.get('package')] = {'prj': prj.get('name'), 'rq_id': req.get('id')}

        return packages_staged

    def extract_specfile_short(self, filelist):
        packages = [spec[:-5] for spec in filelist if re.search(r'\.spec$', spec)]

        return packages

    def get_filelist_for_package(self, pkgname, project, expand=None, extension=None):
        """
        Get a list of files inside a package container
        :param package: the base packagename to be linked to
        :param project: Project to verify
        :param extension: Limit the file list to files with this extension
        """

        filelist = []
        query = {}
        if extension:
            query['extension'] = extension
        if expand:
            query['expand'] = expand

        if len(query):
            url = self.makeurl(['source', project, pkgname], query=query)
        else:
            url = self.makeurl(['source', project, pkgname])
        try:
            content = self.retried_GET(url).read()
            for entry in ET.fromstring(content).findall('entry'):
                filelist.append(entry.attrib['name'])
        except HTTPError as err:
            if err.code == 404 or err.code == 400:
                # The package we were supposed to query does not exist
                # or the sources are broken (as we link into branches it can happen)
                # we can pass this up and return the empty filelist
                return []
            raise err

        return filelist

    def move_between_project(self, source_project, req_id,
                             destination_project):
        """
        Move selected package from one staging to another
        :param source_project: Source project
        :param request: request to move
        :param destination_project: Destination project
        """

        if not self.rm_from_prj(source_project, request_id=req_id):
            return False

        # Copy the package
        return self.rq_to_prj(req_id, destination_project)

    def get_staging_projects(self):
        """
        Get all current running staging projects
        :return list of known staging projects
        """

        result = []
        url = self.makeurl(['staging', self.project, 'staging_projects'])
        status = ET.parse(self.retried_GET(url)).getroot()
        for project in status.findall('staging_project'):
            result.append(project.get('name'))
        return result

    def extract_staging_short(self, p):
        if len(self.cstaging) == 0 or not p.startswith(self.cstaging):
            return p
        prefix = len(self.cstaging) + 1
        return p[prefix:]

    def prj_from_short(self, name):
        if name.startswith(self.cstaging):
            return name
        return '{}:{}'.format(self.cstaging, name)

    def get_staging_projects_short(self, adi=False):
        """
        Get list of staging project by short-hand names.
        :param adi: True for only adi stagings, False for only non-adi stagings,
                    and None for both.
        """
        projects = []
        for project in self.get_staging_projects():
            if adi is not None and self.is_adi_project(project) != adi:
                continue
            short = self.extract_staging_short(project)
            if adi is False and len(short) > 1:
                # Non-letter stagings are not setup for stagingapi.
                continue
            projects.append(short)
        return projects

    def is_adi_project(self, p):
        return ':adi:' in p

    # this function will crash if given a non-adi project name
    def extract_adi_number(self, p):
        return int(p.split(':adi:')[1])

    def get_adi_projects(self):
        """
        Get all current running ADI projects
        :return list of known ADI projects
        """

        projects = [p for p in self.get_staging_projects() if self.is_adi_project(p)]
        return sorted(projects, key=lambda project: self.extract_adi_number(project))

    def find_devel_project_from_adi_frozenlinks(self, prj):
        meta = self.get_prj_pseudometa(prj)
        # the first package's devel project is good enough
        return devel_project_get(self.apiurl, self.project, meta['requests'][0].get('package'))[0]

    def do_change_review_state(self, request_id, newstate, message=None,
                               by_group=None, by_user=None, by_project=None):
        """
        Change review state of the staging request
        :param request_id: id of the request
        :param newstate: state of the new request
        :param message: message for the review
        :param by_group, by_user, by_project: review type
        """

        message = '' if not message else message

        req = get_request(self.apiurl, str(request_id))
        if not req:
            raise oscerr.WrongArgs('Request {} not found'.format(request_id))

        for review in req.reviews:
            if review.by_group == by_group and \
               review.by_user == by_user and \
               review.by_project == by_project and \
               review.state == 'new':

                # call osc's function
                return change_review_state(self.apiurl, str(request_id),
                                           newstate,
                                           message=message,
                                           by_group=by_group,
                                           by_user=by_user,
                                           by_project=by_project)

        return False

    @memoize(session=True)
    def source_info(self, project, package, rev=None):
        query = {'view': 'info'}
        if rev is not None:
            query['rev'] = rev
        url = makeurl(self.apiurl, ('source', project, package), query=query)
        try:
            return ET.parse(http_GET(url)).getroot()
        except (HTTPError, URLError):
            return None

    def source_info_request(self, request):
        action = request.find('action')
        if action.get('type') != 'submit':
            return None

        source = action.find('source')
        return self.source_info(source.get('project'),
                                source.get('package'),
                                source.get('rev'))

    def superseded_request(self, request, target_requests=None):
        """
        Returns a staging info for a request or None
        :param request - a Request instance
        :return dict with 'prj' and 'rq_id' of the old request
        """

        if not target_requests:
            target_requests = []

        # Consolidate all data from request
        request_id = int(request.get('id'))
        action = request.find('action')
        if action is None:
            msg = 'Request {} has no action'.format(request_id)
            raise oscerr.WrongArgs(msg)

        # Where are we targeting the package
        target_project = action.find('target').get('project')
        target_package = action.find('target').get('package')

        # If the values are empty it is no error
        if not target_project or not target_package:
            msg = 'no target/package in request {}, action {}; '
            msg = msg.format(request_id, action)
            logging.info(msg)

        # Only consider if submit or delete and in target_requests if provided.
        is_targeted = (target_package in target_requests or
                       str(request_id) in target_requests)
        if action.get('type') in ['submit', 'delete'] and (
           not (target_requests) or is_targeted):
            stage_info = self.packages_staged.get(target_package)

            # Ensure a request for same package is already staged.
            if stage_info and stage_info['rq_id'] != request_id:
                request_old = get_request(self.apiurl, str(stage_info['rq_id'])).to_xml()
                request_new = request
                replace_old = request_old.find('state').get('name') in ['revoked', 'superseded', 'declined']

                if (request_new.find('action').get('type') == 'delete' and
                        request_old.find('action').get('type') == 'delete'):
                    # Both delete requests.
                    if replace_old:
                        # Pointless since identical requests, but user desires.
                        return stage_info, None
                    else:
                        # Keep the original request and decline this identical one.
                        message = 'sr#{} is an identical delete and is already staged'.format(
                            request_old.get('id'))
                        self.do_change_review_state(request_id, 'declined',
                                                    by_group=self.cstaging_group, message=message)
                        return stage_info, True

                if (request_new.find('action').get('type') !=
                        request_old.find('action').get('type')):
                    # One delete and one submit.
                    if replace_old:
                        if self.ring_packages.get(target_package):
                            # Since deletes are considered ring then both requests are ring and a
                            # supersede is fine.
                            return stage_info, None
                        else:
                            # Unselect old request and do no stage the new request to allow it to be
                            # staged via the normal process to find the appropriate staging project.
                            return stage_info, 'unstage'
                    else:
                        # Decline new type and indicate that old request should be revoked first.
                        message = 'sr#{} of a different type should be revoked first'.format(
                            request_old.get('id'))
                        self.do_change_review_state(request_id, 'declined',
                                                    by_group=self.cstaging_group, message=message)
                        return stage_info, True

                source_info_new = self.source_info_request(request_new)
                source_info_old = self.source_info_request(request_old)

                if source_info_old is None or request_old.find('state').get('name') in ['revoked', 'superseded', 'declined']:
                    # Old source was removed or obsoleted thus new request likely to replace.
                    return stage_info, None

                source_same = source_info_new.get('verifymd5') == source_info_old.get('verifymd5')
                message = 'sr#{} has {} source and is already staged'.format(
                    request_old.get('id'), 'same' if source_same else 'different')
                if source_same:
                    # Keep the original request and decline this identical one.
                    self.do_change_review_state(request_id, 'declined',
                                                by_group=self.cstaging_group, message=message)
                else:
                    # Supersedes request is from the same project
                    if request_new.find('./action/source').get('project') == request_old.find('./action/source').get('project'):
                        message = 'sr#{} has newer source and is from the same project'.format(request_new.get('id'))

                        self.rm_from_prj(stage_info['prj'], request_id=stage_info['rq_id'])
                        self.do_change_review_state(stage_info['rq_id'], 'declined',
                                                    by_group=self.cstaging_group, message=message)
                        return stage_info, None
                    # Ingore the new request pending manual review.
                    IgnoreCommand(self).perform([str(request_id)], message)

                return stage_info, source_same

        return None, None

    def update_superseded_request(self, request, target_requests=None):
        """
        Replace superseded requests that are already in some
        staging prj
        :param request: request we are checking if it is fine
        """
        if not target_requests:
            target_requests = []

        request_id = int(request.get('id'))

        # do not process the request has been excluded
        requests_ignored = self.get_ignored_requests()
        requests_ignored = [rq for rq in requests_ignored.keys()]
        if request_id in requests_ignored:
            return False, False

        stage_info, code = self.superseded_request(request, target_requests)
        if stage_info and (code is None or code == 'unstage'):
            # Remove the old request
            self.rm_from_prj(stage_info['prj'], request_id=stage_info['rq_id'])
            if code is None:
                # Add the new request that should be replacing the old one.
                self.rq_to_prj(request_id, stage_info['prj'])
                self._invalidate_get_open_requests()

        return stage_info, code

    @memoize(session=True)
    def get_ignored_requests(self):
        ignore = {}
        url = self.makeurl(['staging', self.project, 'excluded_requests'])
        root = ET.parse(self.retried_GET(url)).getroot()
        for entry in root.findall('request'):
            ignore[int(entry.get('id'))] = entry.get('description')
        return ignore

    def add_ignored_request(self, request_id, comment):
        url = self.makeurl(['staging', self.project, 'excluded_requests'])
        root = ET.Element('excluded_requests')
        ET.SubElement(root, 'request', {'id': str(request_id), 'description': comment})
        http_POST(url, data=ET.tostring(root))

    def del_ignored_request(self, request_id):
        url = self.makeurl(['staging', self.project, 'excluded_requests'])
        root = ET.Element('excluded_requests')
        ET.SubElement(root, 'request', {'id': str(request_id)})
        http_DELETE(url, data=ET.tostring(root))

    @memoize(session=True, add_invalidate=True)
    def get_open_requests(self, query_extra=None):
        """
        Get all requests with open review for staging project
        that are not yet included in any staging project
        :return list of pending open review requests
        """

        # not using the backlog API of staging workflow as callers
        # expect Request objects

        requests = []

        # xpath query, using the -m, -r, -s options
        where = "@by_group='{}' and @state='new'".format(self.cstaging_group)
        target = "target[@project='{}']".format(self.project)

        query = {'match': f"state/@name='review' and review[{where}] and {target}"}
        if query_extra is not None:
            query.update(query_extra)
        url = self.makeurl(['search', 'request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()

        ignored_requests = self.get_ignored_requests()
        for rq in root.findall('request'):
            if not rq.get('id') in ignored_requests:
                requests.append(rq)
        return requests

    def dispatch_open_requests(self, target_requests=None):
        """
        Verify all requests and dispatch them to staging projects or
        approve them

        """

        if not target_requests:
            target_requests = []

        # get all current pending requests
        self._supersede = True
        requests = self.get_open_requests()
        # check if we can reduce it down by accepting some
        for rq in requests:
            stage_info, code = self.update_superseded_request(rq, target_requests)
            if stage_info:
                yield (stage_info, code, rq)
        self._supersede = False

    def get_prj_meta_revision(self, project):
        log = get_commitlog(self.apiurl, project, '_project', None, format='xml', meta=True)
        root = ET.fromstringlist(log)
        return int(root.find('logentry').get('revision'))

    def get_prj_meta(self, project, revision=None):
        meta = show_project_meta(self.apiurl, project, rev=revision)
        return ET.fromstringlist(meta)

    def get_request_id_for_package(self, project, package):
        """
        Query the request id from meta
        :param project: project the package is in
        :param package: package we want to query for
        """
        data = self.project_status(project, status=False)
        for x in data.findall('staged_requests/request'):
            if x.get('package') == package:
                return int(x.get('id'))
        return None

    def get_package_for_request_id(self, project, request_id):
        """
        Query the request id from meta
        :param project: project the package is in
        :param package: package we want to query for
        """
        data = self.project_status(project, status=False)
        request_id = str(request_id)
        for x in data.findall('staged_requests/request'):
            if x.get('id') == request_id:
                return x.get('package')
        return None

    def rm_from_prj(self, project, package=None, request_id=None):
        """
        Delete request from the project
        :param project: project to remove from
        :param request_id: request we want to remove
        :param review: review state for the review, defautl accepted
        """

        if not request_id:
            request_id = self.get_request_id_for_package(project, package)
        if not package:
            package = self.get_package_for_request_id(project, request_id)
        if not package or not request_id:
            print('no package or no request_id')
            return False

        if self._supersede:
            self.is_package_disabled(project, package, store=True)

        for sub_pkg in self.get_sub_packages(package, project):
            if self._supersede:
                self.is_package_disabled(project, sub_pkg, store=True)

        # Deleting the main package removes local links as well
        self.delete_request(self.project, project, request_id)
        return True

    def delete_request(self, project, stage, request):
        requestxml = f"<requests><request id='{request}'/></requests>"
        u = makeurl(self.apiurl, ['staging', project,
                                  'staging_projects', stage, 'staged_requests'])
        return http_DELETE(u, data=requestxml)

    def is_package_disabled(self, project, package, store=False):
        meta = show_package_meta(self.apiurl, project, package)
        meta = ET.fromstringlist(meta)
        disabled = len(meta.xpath('build/disable[not(@*)]')) > 0
        if store:
            self._package_disabled['/'.join([project, package])] = disabled
        return disabled

    def create_package_container(self, project, package, meta=None, disable_build=False):
        """
        Creates a package container without any fields in project/package
        :param project: project to create it
        :param package: package name
        :param meta: package metadata
        :param disable_build: should the package be created with build
                              flag disabled
        """
        if not meta:
            meta = '<package name="{}"><title/><description/></package>'
            meta = meta.format(package)

        if disable_build:
            root = ET.fromstring(meta)
            elm = ET.SubElement(root, 'build')
            ET.SubElement(elm, 'disable')
            meta = ET.tostring(root)

        url = self.makeurl(['source', project, package, '_meta'])
        http_PUT(url, data=meta)

    def check_ring_packages(self, project, requests):
        """
        Checks if packages from requests are in some ring or not
        :param project: project to check
        :param requests: list of requests to verify
        :return True (has ring packages) / False (has no ring packages)
        """

        for request in requests:
            pkg = self.get_package_for_request_id(project, request)
            if pkg in self.ring_packages:
                return True

        return False

    def rebuild_broken(self, status, check=True):
        """ Rebuild broken packages given a staging's status information. """
        for package in status.findall('broken_packages/package'):
            if package.get('state') == 'unresolvable':
                continue
            key = (package.get('project'), package.get('package'),
                   package.get('repository'), package.get('arch'))
            if check and not self.rebuild_check(*key):
                yield (key, 'skipped')
                continue

            code = rebuild(self.apiurl, *key)
            yield (key, code)

    def rebuild_check(self, project, package, repository, architecture):
        history = self.job_history_get(project, repository, architecture, package)
        fail_count = self.job_history_fail_count(history)
        if fail_count < 3:
            return True

        log = self.buildlog_get(project, package, repository, architecture, -4096)
        if 'Job seems to be stuck here, killed.' in log:
            return True

        return False

    def format_review(self, review):
        if review.get('by_group'):
            return 'group:{}'.format(review.get('by_group'))
        if review.get('by_user'):
            return review.get('by_user')
        if review.get('by_package'):
            return 'package:{}'.format(review.get('by_package'))
        if review.get('by_project'):
            return 'project:{}'.format(review.get('by_project'))
        raise oscerr.WrongArgs('Invalid review')

    def job_history_fail_count(self, history):
        fail_count = 0
        for job in reversed(history.findall('jobhist')):
            if job.get('reason') != 'meta change':
                if job.get('code') == 'failed':
                    fail_count += 1
                else:
                    break
        return fail_count

    # Modfied from osc.core.print_jobhistory()
    def job_history_get(self, project, repository, architecture, package=None, limit=20):
        query = {}
        if package:
            query['package'] = package
        if limit is not None and int(limit) > 0:
            query['limit'] = int(limit)
        u = makeurl(self.apiurl, ['build', project, repository, architecture, '_jobhistory'], query)
        return ET.parse(http_GET(u)).getroot()

    # Modified from osc.core.print_buildlog()
    def buildlog_get(self, prj, package, repository, arch, offset=0, strip_time=False, last=False):
        # to protect us against control characters
        all_bytes = bytes.maketrans(b'', b'')
        remove_bytes = all_bytes[:8] + all_bytes[14:32]  # accept tabs and newlines

        path = ['build', prj, repository, arch, package, '_log']
        if offset < 0:
            url = makeurl(self.apiurl, path, {'view': 'entry'})
            root = ET.parse(http_GET(url)).getroot()
            size = root.xpath('entry[@name="_log"]/@size')
            if size:
                offset += int(size[0])

        query = {'nostream': '1', 'start': '%s' % offset}
        if last:
            query['last'] = 1
        log = StringIO()
        while True:
            query['start'] = offset
            start_offset = offset
            u = makeurl(self.apiurl, path, query)
            for data in streamfile(u, bufsize="line"):
                offset += len(data)
                if strip_time:
                    data = buildlog_strip_time(data)
                log.write(decode_it(data.translate(all_bytes, remove_bytes)))
            if start_offset == offset:
                break

        return log.getvalue()

    def project_status(self, staging, status=True, requests=True, reload=False):
        opts = {}
        if requests:
            opts['requests'] = 1
        if status:
            opts['status'] = 1
        paths = ['staging', self.project, 'staging_projects']
        if staging:
            paths.append(staging)
        url = self.makeurl(paths, opts)
        return ET.parse(self.retried_GET(url)).getroot()

    def project_status_build_percent(self, status):
        final, tobuild = self.project_status_build_sum(status)
        return final / float(final + tobuild) * 100

    def project_status_build_sum(self, status):
        final = tobuild = 0
        for repo in status['building_repositories']:
            final += int(repo['final'])
            tobuild += int(repo['tobuild'])
        return final, tobuild

    def project_status_requests(self, request_type, filter_function=None):
        requests = []
        for status in self.project_status(None, status=False):
            for request in status.findall(f'{request_type}_requests/request'):
                updated_at = dateutil.parser.parse(request.get('updated'), ignoretz=True)
                updated_delta = datetime.utcnow() - updated_at
                if updated_delta.total_seconds() < 0 * 60:
                    # Allow for dashboard to update caches by not considering
                    # requests whose state has changed in the last 5 minutes.
                    continue

                if filter_function and not filter_function(request, updated_delta):
                    continue

                requests.append(str(request.get('id')))

        return requests

    def project_status_final(self, status):
        """Determine if staging project is both active and no longer pending."""
        return status.get('state') in ['acceptable', 'review', 'failed']

    # we use a private function to mock it - httpretty is all or nothing
    def _fetch_project_meta(self, project):
        url = self.makeurl(['source', project, '_project'], {'meta': '1'})
        return http_GET(url).read()

    def days_since_last_freeze(self, project):
        """
        Checks the last update for the frozen links
        :param project: project to check
        :return age in days(float) of the last update
        """
        freezetime = attribute_value_load(self.apiurl, project, 'FreezeTime')
        if freezetime:
            freezetime = dateutil.parser.isoparse(freezetime)
            tz_info = freezetime.tzinfo
            return (datetime.now(tz_info) - freezetime).total_seconds() / 3600 / 24
        # fallback: old method
        url = self.makeurl(['source', project, '_project'], {'meta': '1'})
        root = ET.parse(http_GET(url))
        for entry in root.findall('entry'):
            if entry.get('name') == '_frozenlinks':
                return (time.time() - float(entry.get('mtime'))) / 3600 / 24
        return 100000  # quite some!

    def rq_to_prj(self, request_id, project, remove_exclusion=False):
        """
        Links request to project - delete or submit
        :param request_id: request to link
        :param project: project to link into
        """
        # read info from sr
        act_type = None

        req = get_request(self.apiurl, str(request_id))
        if not req:
            raise oscerr.WrongArgs('Request {} not found'.format(request_id))

        act = req.get_actions('submit')
        if act:
            act_type = 'submit'

        act = req.get_actions('delete')
        if act:
            act_type = 'delete'

        if not act_type:
            msg = 'Request {} is not a submit or delete request'
            msg = msg.format(request_id)
            raise oscerr.WrongArgs(msg)

        requestxml = f"<requests><request id='{request_id}'/></requests>"
        opts = {}
        if remove_exclusion:
            opts['remove_exclusion'] = 1
        u = makeurl(self.apiurl, ['staging', self.project, 'staging_projects', project, 'staged_requests'], opts)
        http_POST(u, data=requestxml)

        if act_type == 'delete':
            self.delete_to_prj(act[0], project)

        if act_type == 'submit':
            self.submit_to_prj(req.get_actions('submit')[0], project)

        return True

    def get_sub_packages(self, package, project):
        """
        Returns a list of packages that need to be linked to main package.
        For adi package, check specfiles according to the main package.
        """
        ret = []

        # Do not trust the layout in the devel project, must to
        # guarantee the sub-pacakges are created according to the
        # specfiles of main package. Therefore, main package must be
        # created before through get_sub_packages().
        filelist = self.get_filelist_for_package(pkgname=package, project=project, expand='1')
        if '_multibuild' in filelist:
            return []

        mainspec = "{}{}".format(package, '.spec')
        if mainspec in filelist:
            filelist.remove(mainspec)
        for file in filelist:
            if file.endswith('.spec'):
                ret.append(file[:-5])

        return ret

    def linked_packages(self, package, project=None):
        if not project:
            project = self.project

        url = self.makeurl(['source', project, package], {'cmd': 'showlinked'})
        f = http_POST(url)
        root = ET.parse(f).getroot()
        result = []
        for package in root.findall('package'):
            result.append({'project': package.get('project'), 'package': package.get('name')})
        return result

    def _wipe_package(self, project, package):
        url = self.makeurl(['build', project],
                           {'cmd': 'wipe', 'package': package})
        try:
            http_POST(url)
        except HTTPError as e:
            print(e.read())
            raise e

    def create_and_wipe_package(self, project, package):
        """
        Helper function for delete requests
        """
        # create build disabled package
        self.create_package_container(project, package, disable_build=True)

        # now trigger wipebinaries to emulate a delete
        self._wipe_package(project, package)

        url = self.makeurl(['source', project, package], {'view': 'getmultibuild'})
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for entry in root.findall('entry'):
            self._wipe_package(project, package + ":" + entry.get('name'))

    def delete_to_prj(self, act, project):
        """
        Hides Package in project
        :param act: action for delete request
        :param project: project to hide in
        """

        tar_pkg = act.tgt_package
        # need to get the subpackages before we wipe it
        sub_packages = self.get_sub_packages(tar_pkg, project)
        self.create_and_wipe_package(project, tar_pkg)

        for sub_pkg in sub_packages:
            self.create_and_wipe_package(project, sub_pkg)

            # create a link so unselect can find it
            root = ET.Element('link', package=tar_pkg, project=project)
            url = self.makeurl(['source', project, sub_pkg, '_link'])
            http_PUT(url, data=ET.tostring(root))

        return tar_pkg

    def submit_to_prj(self, act, project):
        """
        Links sources from request to project
        :param act: action for submit request
        :param project: project to link into
        """

        src_prj = act.src_project
        src_rev = act.src_rev
        src_pkg = act.src_package
        tar_pkg = act.tgt_package

        # If adi project, check for baselibs.conf in all specs to catch both
        # dynamically generated and static baselibs.conf.
        if self.is_adi_project(project):
            baselibs = False
            specfile = source_file_load(self.apiurl, src_prj, src_pkg, '{}.spec'.format(src_pkg), src_rev)
            if specfile and 'baselibs.conf' in specfile:
                baselibs = True
        else:
            baselibs = None

        for sub_pkg in self.get_sub_packages(tar_pkg, project):
            self.create_package_container(project, sub_pkg)

            root = ET.Element('link', package=tar_pkg, cicount='copy')
            url = self.makeurl(['source', project, sub_pkg, '_link'])
            http_PUT(url, data=ET.tostring(root))

            if baselibs is False:
                specfile = source_file_load(self.apiurl, src_prj, src_pkg, '{}.spec'.format(sub_pkg), src_rev)
                if specfile and 'baselibs.conf' in specfile:
                    baselibs = True

        if baselibs:
            # Adi package has baselibs.conf, ensure all staging archs are enabled.
            self.ensure_staging_archs(project)

        return tar_pkg

    def project_meta_url(self, project):
        return self.makeurl(['source', project, '_meta'])

    def ensure_staging_archs(self, project):
        meta = ET.parse(http_GET(self.project_meta_url(project)))
        repository = meta.find('repository[@name="{}"]'.format(self.cmain_repo))

        changed = False
        for arch in self.cstaging_archs:
            if not repository.xpath('./arch[text()="{}"]'.format(arch)):
                elm = ET.SubElement(repository, 'arch')
                elm.text = arch
                changed = True

        if not changed:
            return

        meta = ET.tostring(meta)
        http_PUT(self.project_meta_url(project), data=meta)

        for required_check in self.cstaging_required_checks_adi.split():
            self.add_required_check(project, required_check)

    def prj_from_letter(self, letter):
        if ':' in letter:  # not a letter
            return letter
        return '{}:{}'.format(self.cstaging, letter)

    def adi_prj_from_number(self, number):
        if ':' in str(number):
            return number
        return '{}:adi:{}'.format(self.cstaging, number)

    def list_requests_in_prj(self, project):
        where = "@by_project='%s'+and+@state='new'" % project

        url = self.makeurl(['search', 'request', 'id'],
                           "match=state/@name='review'+and+review[%s]" % where)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        list = []
        for rq in root.findall('request'):
            list.append(int(rq.get('id')))

        return list

    def add_review(self, request_id, by_project=None, by_group=None, msg=None):
        """
        Adds review by project to the request
        :param request_id: request to add review to
        :param project: project to assign review to
        """
        req = get_request(self.apiurl, str(request_id))
        if not req:
            raise oscerr.WrongArgs('Request {} not found'.format(request_id))
        for i in req.reviews:
            if by_project and i.by_project == by_project and i.state == 'new':
                return
            if by_group and i.by_group == by_group and i.state == 'new':
                return

        # don't try to change reviews if the request is dead
        if req.state.name not in ('new', 'review'):
            return

        query = {}
        if by_project:
            query['by_project'] = by_project
            if not msg:
                msg = 'Being evaluated by staging project "{}"'
                msg = msg.format(by_project)
        if by_group:
            query['by_group'] = by_group
            if not msg:
                msg = 'Being evaluated by group "{}"'.format(by_group)
        if not query:
            raise oscerr.WrongArgs('We need a group or a project')
        query['cmd'] = 'addreview'
        url = self.makeurl(['request', str(request_id)], query)
        http_POST(url, data=msg)

    def get_flag_in_prj(self, project, flag='build', repository=None, arch=None):
        """Return the flag value in a project."""
        url = self.makeurl(['source', project, '_meta'])
        root = ET.parse(http_GET(url)).getroot()
        section = root.find(flag)
        if section is None:
            # the default for build and publish (is all we care for)
            return 'enable'
        for status in section:
            is_repository = status.get('repository', None) == repository
            is_arch = status.get('arch', None) == arch
            if is_repository and is_arch:
                return status.tag

    def switch_flag_in_prj(self, project, flag='build', state='disable', repository=None, arch=None):
        query = {'cmd': 'set_flag', 'flag': flag, 'status': state}
        if repository:
            query['repository'] = repository
        if arch:
            query['arch'] = arch
        url = self.makeurl(['source', project], query)
        http_POST(url)

    def build_switch_prj(self, project, state, repository=None, arch=None):
        """
        Switch build state of project to desired state
        :param project: project to switch state for
        :param state: desired state for build
        """
        self.switch_flag_in_prj(project, flag='build', state=state, repository=repository, arch=arch)

    def prj_frozen_enough(self, project):
        """
        Check if we can and should refreeze the prj"
        :param project the project to check
        :returns True if we can select into it
        """

        data = self.project_status(project)
        if data.get('state') != 'empty':
            return True  # already has content

        # young enough
        if self.days_since_last_freeze(project) < MAX_FROZEN_AGE:
            return True

        return False

    def build_switch_staging_project(self, target_project, target_flag):
        """
        Switch the build flag for a staging project
        :param target_project: staging project
        :param target_flag: build target flag
        """
        self.build_switch_prj(target_project, target_flag)

    def item_exists(self, project, package=None):
        """
        Return true if the given project exists
        :param project: project name to check
        :param package: optional package to check
        """
        return entity_exists(self.apiurl, project, package)

    def package_version(self, project, package):
        """
        Return the version of a package, None in case the package does not exist
        The first non-commented Version: tag found is used.
        :param project: the project the package resides in
        :param package: the package to check
        :param product: if passed, the package to be checked is considered to be part of _product
        """
        if not self.item_exists(project, package):
            return None

        version = None

        specfile = source_file_load(self.apiurl, project, package, '{}.spec'.format(package))
        if specfile:
            try:
                version = re.findall('^Version:(.*)', specfile, re.MULTILINE)[0].strip()
            except IndexError:
                pass
        return version

    def get_binary_version(self, project, rpm, repository='standard', arch='x86_64'):
        """
        Return the version of a built rpm file
        """
        url = self.makeurl(['build', project, repository, arch, '_repository', "%s?view=fileinfo" % rpm])
        try:
            return ET.parse(http_GET(url)).getroot().find('version').text
        except HTTPError as e:
            if e.code == 404:
                return None
            raise

    def pseudometa_file_load(self, filename, revision=None):
        return project_pseudometa_file_load(self.apiurl, self.project, filename, revision)

    def pseudometa_file_save(self, filename, content, comment=None):
        project_pseudometa_file_save(self.apiurl, self.project, filename, content, comment)

    def pseudometa_file_ensure(self, filename, content, comment=None):
        project_pseudometa_file_ensure(self.apiurl, self.project, filename, content, comment)

    def attribute_value_load(self, name):
        return attribute_value_load(self.apiurl, self.project, name)

    def attribute_value_save(self, name, value):
        return attribute_value_save(self.apiurl, self.project, name, value)

    def update_status_or_deactivate(self, project, command):
        root = self.project_status(project, reload=True)
        if root.get('state') == 'empty':
            # Cleanup like accept since the staging is now empty.
            if self.is_adi_project(project):
                self.delete_empty_adi_project(project)
            else:
                self.staging_deactivate(project)
        else:
            self.build_switch_staging_project(project, 'enable')

    def accept_status_comment(self, project, packages):
        if not len(packages):
            # Avoid making accept comments for empty projects which can occur
            # when all requests are unselected or something like #1142.
            return

        # A single comment should be enough to notify everybody, since they are
        # already mentioned in the comments created by select/unselect.
        comment = 'Project "{}" accepted. ' \
            'The following packages have been submitted to {}: {}.'.format(
                project, self.project, ', '.join(packages))
        CommentAPI(self.apiurl).add_comment(project_name=project, comment=comment)

    def get_prj_results(self, prj, arch):
        url = self.makeurl(['build', prj, 'standard', arch, "_jobhistory?code=lastfailures"])
        results = []

        root = ET.parse(http_GET(url)).getroot()

        xmllines = root.findall("./jobhist")

        for pkg in xmllines:
            if pkg.attrib['code'] == 'failed':
                results.append(pkg.attrib['package'])

        return results

    def is_repo_dirty(self, project, repository):
        url = self.makeurl(['build', project, '_result?code=broken&repository=%s' % repository])
        root = ET.parse(http_GET(url)).getroot()
        for repo in root.findall('result'):
            repostate = repo.get('state', 'missing')
            if repostate not in ['unpublished', 'published'] or repo.get('dirty', 'false') == 'true':
                return True
        return False

    def list_packages(self, project):
        url = self.makeurl(['source', project])
        pkglist = []

        root = ET.parse(http_GET(url)).getroot()
        xmllines = root.findall("./entry")
        for pkg in xmllines:
            pkglist.append(pkg.attrib['name'])

        return pkglist

    def check_pkgs(self, pkg_list):
        """Filter the package list to those still existing in the project"""
        project_packages = set(self.list_packages(self.project))
        ret = list()
        for pkg in pkg_list:
            # map multibuild flavors
            source = pkg.split(':')[0]
            if source in project_packages:
                ret.append(pkg)
        return ret

    def rebuild_pkg(self, package, prj, arch, code=None):
        query = {
            'cmd': 'rebuild',
            'arch': arch
        }
        if package:
            query['package'] = package
        pkg = query['package']

        u = self.makeurl(['build', prj], query=query)

        try:
            print("tried to trigger rebuild for project '%s' package '%s'" % (prj, pkg))
            http_POST(u)
        except HTTPError:
            print("could not trigger rebuild for project '%s' package '%s'" % (prj, pkg))

    def _candidate_adi_project(self):
        """Decide a candidate name for an ADI project."""
        adi_projects = self.get_adi_projects()
        adi_index = 1
        for i, project in enumerate(adi_projects):
            adi_index = i + 1
            if not project.endswith(str(adi_index)):
                return self.adi_prj_from_number(adi_index)
            adi_index = i + 2
        return self.adi_prj_from_number(adi_index)

    def update_adi_frozenlinks(self, name, src_prj):
        xpath = {
            'package': "@project='%s' and devel/@project='%s'" % (self.project, src_prj),
        }
        collection = search(self.apiurl, **xpath)['package']

        # all packages had matched devel project defined
        pkglist = [p.attrib['name'] for p in collection.findall('package')]

        flink = ET.Element('frozenlinks')
        fl_prj = ET.SubElement(flink, 'frozenlink', {'project': self.project})

        project_sourceinfo = ET.fromstring(show_project_sourceinfo(self.apiurl, self.project, True))
        for si in project_sourceinfo.findall('sourceinfo'):
            pkg = si.get('package')
            if pkg in pkglist:
                ET.SubElement(fl_prj, 'package', {'name': pkg, 'srcmd5': si.get('srcmd5'), 'vrev': si.get('vrev')})
            # check multiple spec ie. sub-package
            for linked in si.findall('linked'):
                if linked.get('package') in pkglist:
                    ET.SubElement(fl_prj, 'package', {'name': pkg, 'srcmd5': si.get('lsrcmd5'), 'vrev': si.get('vrev')})

        # commit frozenlinks
        url = self.makeurl(['source', name, '_project', '_frozenlinks'], {'meta': '1'})
        link = ET.tostring(flink)
        http_PUT(url, data=link)

    def create_adi_project(self, name, use_frozenlinks=False, src_prj=None):
        """Create an ADI project."""
        if not name:
            name = self._candidate_adi_project()
        else:
            name = self.adi_prj_from_number(name)

        adi_projects = self.get_adi_projects()
        if name in adi_projects:
            raise Exception('Project {} already exist'.format(name))

        if use_frozenlinks:
            linkproject = '<link project="{}"/>'.format(self.project)
            repository = '<repository name="standard" rebuild="direct" linkedbuild="all">'
        else:
            linkproject = ''
            repository = '<repository name="standard">'

        # Add "images" and "containerfile" repos if the main project has them.
        # If both are present, they build against each other and the parent project.

        images_repo = ''
        if self.project_has_repo('images'):
            containerfile_path = ''
            if self.project_has_repo('containerfile'):
                containerfile_path = f'<path project="{name}" repository="containerfile"/>'

            images_repo = f"""
               <repository name="images">
                 <path project="{name}" repository="standard"/>
                 {containerfile_path}
                 <path project="{self.cstaging}" repository="standard"/>
                 <path project="{self.project}" repository="images"/>
                 <arch>x86_64</arch>
              </repository>"""

        containerfile_repo = ''
        if self.project_has_repo('containerfile'):
            images_path = ''
            if self.project_has_repo('images'):
                images_path = f'<path project="{name}" repository="images"/>'

            containerfile_repo = f"""
               <repository name="containerfile">
                 <path project="{name}" repository="standard"/>
                 {images_path}
                 <path project="{self.cstaging}" repository="standard"/>
                 <path project="{self.project}" repository="containerfile"/>
                 <arch>x86_64</arch>
              </repository>"""

        meta = f"""
        <project name="{name}">
          <title></title>
          <description></description>
          {linkproject}
          <url>/staging_workflows/{self.project}/staging_projects/{self.project}:Staging:adi:{self.extract_adi_number(name)}</url>
          <publish>
            <disable/>
          </publish>
          <debuginfo>
            <enable/>
          </debuginfo>
          {repository}
            <path project="{self.cstaging}" repository="standard"/>
            <path project="{self.project}" repository="standard"/>
          </repository>
          {images_repo}
          {containerfile_repo}
        </project>"""

        root = ET.fromstring(meta)
        repository = root.find('.//repository[@name="standard"]')
        for arch in self.cstaging_archs:
            a = ET.SubElement(repository, 'arch')
            a.text = arch
        meta = ET.tostring(root)
        url = make_meta_url('prj', name, self.apiurl)
        http_PUT(url, data=meta)
        # put twice because on first put, the API adds useless maintainer
        http_PUT(url, data=meta)

        self.register_new_staging_project(name)

        if use_frozenlinks:
            self.update_adi_frozenlinks(name, src_prj)

        for required_check in self.cstaging_required_checks_adi.split():
            self.add_required_check(name, required_check)
        return name

    def add_required_check(self, project, check):
        root = ET.Element('required_checks')
        name = ET.SubElement(root, 'name')
        name.text = check

        meta = ET.parse(http_GET(self.project_meta_url(project)))
        repository = meta.find('repository[@name="{}"]'.format(self.cmain_repo))

        for arch_element in repository.findall('arch'):
            architecture = arch_element.text
            url = self.makeurl(['status_reports', 'built_repositories', project,
                                self.cmain_repo, architecture, 'required_checks'])
            http_POST(url, data=ET.tostring(root))

    def register_new_staging_project(self, name):
        data = '<workflow><staging_project>{}</staging_project></workflow>'.format(name)
        url = self.makeurl(['staging', self.project, 'staging_projects'])
        try:
            http_POST(url, data=data)
        except HTTPError as e:
            # if not using staging workflow, this will fail
            if e.code != 400:
                raise e

    def is_user_member_of(self, user, group):
        root = ET.fromstring(get_group(self.apiurl, group))

        if root.findall("./person/person[@userid='%s']" % user):
            return True
        else:
            return False

    def staging_deactivate(self, project):
        """Cleanup staging after last request is removed and disable building."""

        # Clear all comments.
        CommentAPI(self.apiurl).delete_from(project_name=project)

        self.build_switch_staging_project(project, 'disable')

    def ignore_format(self, request_id):
        requests_ignored = self.get_ignored_requests()
        if int(request_id) in requests_ignored.keys():
            ignore_indent = ' ' * (2 + len(str(request_id)) + 1)
            return textwrap.fill(str(requests_ignored[request_id]),
                                 initial_indent=ignore_indent,
                                 subsequent_indent=ignore_indent,
                                 break_long_words=False)

        return None

    def is_staging_bootstrapped(self, project):
        if self.rings:
            # Determine if staging is bootstrapped.
            meta = self.get_prj_meta(project)
            xpath = 'link[@project="{}"]'.format(self.rings[0])
            return meta.find(xpath) is not None

        return False

    def delete_empty_adi_project(self, project):
        if not self.is_adi_project(project):
            return

        if not self.is_staging_manager:
            return

        try:
            delete_project(self.apiurl, project, force=True)
        except HTTPError as e:
            print(e)
