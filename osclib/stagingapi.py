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

import json
import logging
import urllib2
import time
import re
from xml.etree import cElementTree as ET

import yaml

from osc import conf
from osc import oscerr
from osc.core import change_review_state
from osc.core import delete_package
from osc.core import get_group
from osc.core import get_request
from osc.core import make_meta_url
from osc.core import makeurl
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT

from osclib.comments import CommentAPI
from osclib.memoize import memoize


class StagingAPI(object):
    """
    Class containing various api calls to work with staging projects.
    """

    def __init__(self, apiurl, project):
        """Initialize instance variables."""

        self.apiurl = apiurl
        self.project = project

        # Store some prefix / data used in the code.
        self.cstaging = conf.config[project]['staging']
        self.cstaging_group = conf.config[project]['staging-group']
        self.cstaging_archs = conf.config[project]['staging-archs'].split()
        self.cstaging_dvd_archs = conf.config[project]['staging-dvd-archs'].split()
        self.cstaging_nocleanup = conf.config[project]['nocleanup-packages'].split()
        self.crings = conf.config[project]['rings']
        self.cnonfree = conf.config[project]['nonfree']
        self.crebuild = conf.config[project]['rebuild']
        self.cproduct = conf.config[project]['product']
        self.copenqa = conf.config[project]['openqa']
        self.user = conf.get_apiurl_usr(apiurl)
        self._ring_packages = None
        self._ring_packages_for_links = None
        self._packages_staged = None
        self._package_metas = dict()

        # If the project support rings, inititialize some variables.
        if self.crings:
            self.rings = (
                '{}:0-Bootstrap'.format(self.crings),
                '{}:1-MinimalX'.format(self.crings),
                '{}:2-TestDVD'.format(self.crings)
            )
        else:
            self.rings = []


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

    def makeurl(self, l, query=None):
        """
        Wrapper around osc's makeurl passing our apiurl
        :return url made for l and query
        """
        query = [] if not query else query
        return makeurl(self.apiurl, l, query)

    def _retried_request(self, url, func, data=None):
        retry_sleep_seconds = 1
        while True:
            try:
                if data is not None:
                    return func(url, data=data)
                return func(url)
            except urllib2.HTTPError, e:
                if 500 <= e.code <= 599:
                    print 'Error {}, retrying {} in {}s'.format(e.code, url, retry_sleep_seconds)
                    time.sleep(retry_sleep_seconds)
                    # increase sleep time up to one minute to avoid hammering
                    # the server in case of real problems
                    if (retry_sleep_seconds % 60):
                        retry_sleep_seconds += 1
                else:
                    raise e

    def retried_GET(self, url):
        return self._retried_request(url, http_GET)

    def retried_POST(self, url):
        return self._retried_request(url, http_POST)

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
                # XXX TODO - Test-DVD-x86_64 is hardcoded here
                if pkg in ret and not pkg.startswith('Test-DVD-'):
                    msg = '{} is defined in two projects ({} and {})'
                    if checklinks and pkg in except_pkgs and prj == except_pkgs[pkg]:
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
        for prj in self.get_staging_projects():
            meta = self.get_prj_pseudometa(prj)
            for req in meta['requests']:
                packages_staged[req['package']] = {'prj': prj, 'rq_id': req['id']}

        return packages_staged

    def get_package_information(self, project, pkgname, rev=None):
        """
        Get the revision packagename and source project to copy from
        based on content provided
        :param project: the project we are having the package in
        :param pkgname: name of the package we want to identify
        :return dict ( project, package, revision, md5sum )
        """

        package_info = {}

        query = {
            'rev': rev
        }
        if rev:
            url = self.makeurl(['source', project, pkgname], query=query)
        else:
            url = self.makeurl(['source', project, pkgname])
        content = http_GET(url)
        root = ET.parse(content).getroot()
        package_info['dir_srcmd5'] = root.attrib['srcmd5']

        linkinfo = root.find('linkinfo')
        package_info['srcmd5'] = linkinfo.attrib['srcmd5']
        package_info['rev'] = linkinfo.attrib.get('rev', None)
        package_info['project'] = linkinfo.attrib['project']
        package_info['package'] = linkinfo.attrib['package']

        return package_info

    def get_filelist_for_package(self, pkgname, project, extension=None):
        """
        Get a list of files inside a package container
        :param package: the base packagename to be linked to
        :param project: Project to verify
        :param extension: Limit the file list to files with this extension
        """

        filelist = []
        query = {
            'extension': extension
        }

        if extension:
            url = self.makeurl(['source', project, pkgname], query=query)
        else:
            url = self.makeurl(['source', project, pkgname])
        try:
            content = http_GET(url)
            for entry in ET.parse(content).getroot().findall('entry'):
                filelist.append(entry.attrib['name'])
        except urllib2.HTTPError, err:
            if err.code == 404:
                # The package we were supposed to query does not exist
                # we can pass this up and return the empty filelist
                pass

        return filelist

    def move_between_project(self, source_project, req_id,
                             destination_project):
        """
        Move selected package from one staging to another
        :param source_project: Source project
        :param request: request to move
        :param destination_project: Destination project
        """

        # Get the relevant information about source
        meta = self.get_prj_pseudometa(source_project)
        found = False
        for req in meta['requests']:
            if int(req['id']) == int(req_id):
                found = True
                break
        if not found:
            return None

        # Copy the package
        self.rq_to_prj(req_id, destination_project)
        # Delete the old one
        self.rm_from_prj(source_project, request_id=req_id,
                         msg='Moved to {}'.format(destination_project))

        # Build disable the old project if empty
        self.build_switch_staging_project(source_project)

        return True

    def get_staging_projects(self):
        """
        Get all current running staging projects
        :return list of known staging projects
        """

        projects = []

        query = "id?match=starts-with(@name,'{}:')".format(self.cstaging)
        url = self.makeurl(['search', 'project', query])
        projxml = http_GET(url)
        root = ET.parse(projxml).getroot()
        for val in root.findall('project'):
            projects.append(val.get('name'))
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

    def accept_non_ring_request(self, request):
        """
        Accept review of requests that are not yet in any ring so we
        don't delay their testing.
        :param request: request to check
        """

        # Consolidate all data from request
        request_id = int(request.get('id'))
        action = request.findall('action')
        if not action:
            msg = 'Request {} has no action'.format(request_id)
            raise oscerr.WrongArgs(msg)
        # we care only about first action
        action = action[0]

        # Where are we targeting the package
        target_project = action.find('target').get('project')
        target_package = action.find('target').get('package')

        # If the values are empty it is no error
        if not target_project or not target_package:
            msg = 'no target/package in request {}, action {}; '
            msg = msg.format(request_id, action)
            logging.info(msg)

        # Verify the package ring
        ring = self.ring_packages.get(target_package, None)
        if not ring:
            # accept the request here
            message = 'No need for staging, not in tested ring projects.'
            self.do_change_review_state(request_id, 'accepted', message=message,
                                        by_group=self.cstaging_group)

    def supseded_request(self, request, target_pkgs=None):
        """
        Returns a staging info for a request or None
        :param request - a Request instance
        :return dict with 'prj' and 'rq_id' of the old request
        """

        if not target_pkgs:
            target_pkgs = []

        # Consolidate all data from request
        request_id = int(request.get('id'))
        action = request.findall('action')
        if not action:
            msg = 'Request {} has no action'.format(request_id)
            raise oscerr.WrongArgs(msg)
        # we care only about first action
        action = action[0]

        # Where are we targeting the package
        target_project = action.find('target').get('project')
        target_package = action.find('target').get('package')

        # If the values are empty it is no error
        if not target_project or not target_package:
            msg = 'no target/package in request {}, action {}; '
            msg = msg.format(request_id, action)
            logging.info(msg)

        pkg_do_supersede = True
        if target_pkgs:
            if action.get('type') not in ['submit', 'delete'] or target_package not in target_pkgs:
                pkg_do_supersede = False

        # If the package is currently tracked then we do the replacement
        stage_info = self.packages_staged.get(target_package, {'prj': '', 'rq_id': 0})
        if pkg_do_supersede and int(stage_info['rq_id']) != 0 and int(stage_info['rq_id']) != request_id:
            return stage_info
        return None

    def update_superseded_request(self, request, target_pkgs=None):
        """
        Replace superseded requests that are already in some
        staging prj
        :param request: request we are checking if it is fine
        """
        if not target_pkgs:
            target_pkgs = []

        stage_info = self.supseded_request(request, target_pkgs)
        request_id = int(request.get('id'))

        if stage_info:
            # Remove the old request
            self.rm_from_prj(stage_info['prj'],
                             request_id=stage_info['rq_id'],
                             msg='Replaced by newer request',
                             review='declined')
            # Add the new one that should be replacing it
            self.rq_to_prj(request_id, stage_info['prj'])
            return True
        return False

    def get_open_requests(self):
        """
        Get all requests with open review for staging project
        that are not yet included in any staging project
        :return list of pending open review requests
        """

        requests = []

        # xpath query, using the -m, -r, -s options
        where = "@by_group='{}'+and+@state='new'".format(self.cstaging_group)
        projects = [format(self.project)]
        if self.cnonfree:
            projects.append(self.cnonfree)
        targets = ["target[@project='{}']".format(p) for p in projects]

        query = "match=state/@name='review'+and+review[{}]+and+({})".format(
            where, '+or+'.join(targets))
        url = self.makeurl(['search', 'request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for rq in root.findall('request'):
            requests.append(rq)
        return requests

    def dispatch_open_requests(self, packages=None):
        """
        Verify all requests and dispatch them to staging projects or
        approve them

        """

        if not packages:
            packages = []

        # get all current pending requests
        requests = self.get_open_requests()
        # check if we can reduce it down by accepting some
        for rq in requests:
            # if self.crings:
            #     self.accept_non_ring_request(rq)
            self.update_superseded_request(rq, packages)

    @memoize(ttl=60, session=True, add_invalidate=True)
    def get_prj_pseudometa(self, project):
        """
        Gets project data from YAML in project description
        :param project: project to read data from
        :return structured object with metadata
        """

        url = make_meta_url('prj', project, self.apiurl)
        f = http_GET(url)
        root = ET.parse(f).getroot()
        description = root.find('description')
        # If YAML parsing fails, load default
        # FIXME: Better handling of errors
        # * broken description
        # * directly linked packages
        # * removed linked packages
        try:
            data = yaml.load(description.text)
        except (TypeError, AttributeError):
            data = {}
        # make sure we have a requests field
        data['requests'] = data.get('requests', [])
        return data

    def set_prj_pseudometa(self, project, meta):
        """
        Sets project description to the YAML of the provided object
        :param project: project to save into
        :param meta: data to save
        """

        # Get current metadata
        url = make_meta_url('prj', project, self.apiurl)
        root = ET.parse(http_GET(url)).getroot()
        # Find description
        description = root.find('description')
        # Order the requests and replace it with yaml
        meta['requests'] = sorted(meta['requests'], key=lambda x: x['id'])
        description.text = yaml.dump(meta)
        # Find title
        title = root.find('title')
        # Put something nice into title as well
        new_title = []
        for request in meta['requests']:
            new_title.append(request['package'])
        nt = ', '.join(sorted(new_title))
        title.text = nt[:240]
        # Write XML back
        url = make_meta_url('prj', project, self.apiurl, force=True)
        http_PUT(url, data=ET.tostring(root))

        # Invalidate here the cache for this stating project
        self._invalidate_get_prj_pseudometa(project)

    def _add_rq_to_prj_pseudometa(self, project, request_id, package):
        """
        Records request as part of the project within metadata
        :param project: project to record into
        :param request_id: request id to record
        :param package: package the request is about
        """

        data = self.get_prj_pseudometa(project)
        append = True
        for request in data['requests']:
            if request['package'] == package:
                # Only update if needed (to save calls to get_request)
                if request['id'] != request_id or not request.get('author'):
                    request['id'] = request_id
                    request['author'] = get_request(self.apiurl, str(request_id)).get_creator()
                append = False
        if append:
            author = get_request(self.apiurl, str(request_id)).get_creator()
            data['requests'].append({'id': request_id, 'package': package, 'author': author})
        self.set_prj_pseudometa(project, data)

    def get_request_id_for_package(self, project, package):
        """
        Query the request id from meta
        :param project: project the package is in
        :param package: package we want to query for
        """
        data = self.get_prj_pseudometa(project)
        for x in data['requests']:
            if x['package'] == package:
                return int(x['id'])
        return None

    def get_package_for_request_id(self, project, request_id):
        """
        Query the request id from meta
        :param project: project the package is in
        :param package: package we want to query for
        """
        data = self.get_prj_pseudometa(project)
        request_id = int(request_id)
        for x in data['requests']:
            if x['id'] == request_id:
                return x['package']
        return None

    def _remove_package_from_prj_pseudometa(self, project, package):
        """
        Delete request from the project pseudometa
        :param project: project to remove from
        :param package: package we want to remove from meta
        """

        data = self.get_prj_pseudometa(project)
        data['requests'] = filter(lambda x: x['package'] != package, data['requests'])
        self.set_prj_pseudometa(project, data)

    def rm_from_prj(self, project, package=None, request_id=None,
                    msg=None, review='accepted'):
        """
        Delete request from the project
        :param project: project to remove from
        :param request_id: request we want to remove
        :param msg: message for the log
        :param review: review state for the review, defautl accepted
        """

        if not request_id:
            request_id = self.get_request_id_for_package(project, package)
        if not package:
            package = self.get_package_for_request_id(project, request_id)
        if not package or not request_id:
            return

        self._remove_package_from_prj_pseudometa(project, package)
        subprj = self.map_ring_package_to_subject(project, package)
        delete_package(self.apiurl, subprj, package, force=True, msg=msg)

        for sub_prj, sub_pkg in self.get_sub_packages(package):
            sub_prj = self.map_ring_package_to_subject(project, sub_pkg)
            if sub_prj != subprj:  # if different to the main package's prj
                delete_package(self.apiurl, sub_prj, sub_pkg, force=True, msg=msg)

        self.set_review(request_id, project, state=review, msg=msg)

    def create_package_container(self, project, package, disable_build=False):
        """
        Creates a package container without any fields in project/package
        :param project: project to create it
        :param package: package name
        :param disable_build: should the package be created with build
                              flag disabled
        """
        dst_meta = '<package name="{}"><title/><description/></package>'
        dst_meta = dst_meta.format(package)
        if disable_build:
            root = ET.fromstring(dst_meta)
            elm = ET.SubElement(root, 'build')
            ET.SubElement(elm, 'disable')
            dst_meta = ET.tostring(root)

        url = self.makeurl(['source', project, package, '_meta'])
        http_PUT(url, data=dst_meta)

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

    def check_project_status(self, project):
        """
        Checks a staging project for acceptance. Use the JSON document
        for staging project to base the decision.
        :param project: project to check
        :return true (ok)/false (empty prj) or list of strings with
                informations)

        """
        _prefix = '{}:'.format(self.cstaging)
        if project.startswith(_prefix):
            project = project.replace(_prefix, '')

        query = {'format': 'json'}
        url = self.makeurl(('project',  'staging_projects', self.project, project),
                           query=query)
        result = json.load(self.retried_GET(url))
        return result and result['overall_state'] == 'acceptable'

    def days_since_last_freeze(self, project):
        """
        Checks the last update for the frozen links
        :param project: project to check
        :return age in days(float) of the last update
        """
        url = self.makeurl(['source', project, '_project'], {'meta': '1'})
        root = ET.parse(http_GET(url)).getroot()
        for entry in root.findall('entry'):
            if entry.get('name') == '_frozenlinks':
                return (time.time() - float(entry.get('mtime')))/3600/24
        return 100000  # quite some!

    def rq_to_prj(self, request_id, project):
        """
        Links request to project - delete or submit
        :param request_id: request to link
        :param project: project to link into
        """
        # read info from sr
        tar_pkg = None

        req = get_request(self.apiurl, str(request_id))
        if not req:
            raise oscerr.WrongArgs('Request {} not found'.format(request_id))

        act = req.get_actions('submit')
        if act:
            tar_pkg = self.submit_to_prj(act[0], project)

        act = req.get_actions('delete')
        if act:
            tar_pkg = self.delete_to_prj(act[0], project)

        if not tar_pkg:
            msg = 'Request {} is not a submit or delete request'
            msg = msg.format(request_id)
            raise oscerr.WrongArgs(msg)

        # register the package name
        self._add_rq_to_prj_pseudometa(project, int(request_id), tar_pkg)

        # add review
        self.add_review(request_id, project)

        # now remove the staging checker
        self.do_change_review_state(request_id, 'accepted',
                                    by_group=self.cstaging_group,
                                    message='Picked {}'.format(project))
        return True

    def map_ring_package_to_subject(self, project, pkg):
        """
        Returns the subproject (if any) to use for the pkg depending on the ring
        the package is in
        :param project the staging prj
        :param pkg the package to add
        """
        # it's actually a pretty stupid algorithm, but it might become more complex later

        if project.endswith(':DVD'):
            return project  # not yet

        ring_dvd = '{}:2-TestDVD'.format(self.crings)
        if self.ring_packages.get(pkg) == ring_dvd:
            if not self.item_exists(project + ":DVD") and self.item_exists(project, pkg):
                # assuming it is in adi staging, workaround for https://progress.opensuse.org/issues/9646
                return project
            else:
                return project + ":DVD"

        return project

    def get_sub_packages(self, pkg, project=None):
        """
        Returns a list of packages that need to be linked into rings
        too. A package is actually a tuple of project and package name
        """
        ret = []
        if not project:
            project = self.ring_packages.get(pkg)
        if not project:
            return ret
        url = self.makeurl(['source', project, pkg],
                           {'cmd': 'showlinked'})

        # showlinked is a POST for rather bizzare reasons
        f = http_POST(url)
        root = ET.parse(f).getroot()

        for pkg in root.findall('package'):
            ret.append((pkg.get('project'), pkg.get('name')))

        return ret

    def create_and_wipe_package(self, project, package):
        """
        Helper function for delete requests
        """
        # create build disabled package
        self.create_package_container(project, package, disable_build=True)

        # now trigger wipebinaries to emulate a delete
        url = self.makeurl(['build', project],
                           {'cmd': 'wipe', 'package': package})
        http_POST(url)

    def delete_to_prj(self, act, project):
        """
        Hides Package in project
        :param act: action for delete request
        :param project: project to hide in
        """

        tar_pkg = act.tgt_package
        project = self.map_ring_package_to_subject(project, tar_pkg)
        self.create_and_wipe_package(project, tar_pkg)

        for sub_prj, sub_pkg in self.get_sub_packages(tar_pkg):
            sub_prj = self.map_ring_package_to_subject(project, sub_pkg)
            self.create_and_wipe_package(sub_prj, sub_pkg)

            # create a link so unselect can find it
            root = ET.Element('link', package=tar_pkg, project=project)
            url = self.makeurl(['source', sub_prj, sub_pkg, '_link'])
            http_PUT(url, data=ET.tostring(root))

        return tar_pkg

    def submit_to_prj(self, act, project, force_enable_build=False):
        """
        Links sources from request to project
        :param act: action for submit request
        :param project: project to link into
        :param force_enable_build: overwrite the ring criteria to enable
               or disable the build
        """

        src_prj = act.src_project
        src_rev = act.src_rev
        src_pkg = act.src_package
        tar_pkg = act.tgt_package

        disable_build = False
        # The force_enable_build will avoid the
        # map_ring_package_to_subproject
        if not force_enable_build:
            if self.crings and not self.ring_packages.get(tar_pkg) and not self.is_adi_project(project):
                disable_build = True
                logging.warning("{}/{} not in ring, build disabled".format(project, tar_pkg))
            else:
                project = self.map_ring_package_to_subject(project, tar_pkg)

        self.create_package_container(project, tar_pkg,
                                      disable_build=disable_build)

        # expand the revision to a md5
        url = self.makeurl(['source', src_prj, src_pkg],
                           {'rev': src_rev, 'expand': 1})
        f = http_GET(url)
        root = ET.parse(f).getroot()
        src_rev = root.attrib['srcmd5']
        src_vrev = root.attrib.get('vrev')

        # link stuff - not using linkpac because linkpac copies meta
        # from source
        root = ET.Element('link', package=src_pkg, project=src_prj,
                          rev=src_rev)
        if src_vrev:
            root.attrib['vrev'] = src_vrev
        url = self.makeurl(['source', project, tar_pkg, '_link'])
        http_PUT(url, data=ET.tostring(root))

        for sub_prj, sub_pkg in self.get_sub_packages(tar_pkg):
            sub_prj = self.map_ring_package_to_subject(project, sub_pkg)
            # print project, tar_pkg, sub_pkg, sub_prj
            if sub_prj == project:  # skip inner-project links
                continue
            self.create_package_container(sub_prj, sub_pkg)

            root = ET.Element('link', package=tar_pkg, project=project)
            url = self.makeurl(['source', sub_prj, sub_pkg, '_link'])
            http_PUT(url, data=ET.tostring(root))

        return tar_pkg

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

    def set_review(self, request_id, project, state='accepted', msg=None):
        """
        Sets review for request done by project
        :param request_id: request to change review for
        :param project: project to do the review
        """
        req = get_request(self.apiurl, str(request_id))
        if not req:
            raise oscerr.WrongArgs('Request {} not found'.format(request_id))
        # don't try to change reviews if the request is dead
        if req.state.name not in ('new', 'review'):
            return
        cont = False
        for i in req.reviews:
            if i.by_project == project and i.state == 'new':
                cont = True
        if not cont:
            return
        if not msg:
            msg = 'Reviewed by staging project "{}" with result: "{}"'
            msg = msg.format(project, state)
        self.do_change_review_state(request_id, state, by_project=project,
                                    message=msg)

    def get_flag_in_prj(self, project, flag='build', repository=None, arch=None):
        """Return the flag value in a project."""
        url = self.makeurl(['source', project, '_meta'])
        root = ET.parse(http_GET(url)).getroot()
        section = root.find(flag)
        for status in section:
            is_repository = status.get('repository', None) == repository
            is_arch = status.get('arch', None) == arch
            if is_repository and is_arch:
                return status.tag

    def switch_flag_in_prj(self, project, flag='build', state='disable', repository=None, arch=None):
        url = self.makeurl(['source', project, '_meta'])
        prjmeta = ET.parse(http_GET(url)).getroot()

        flagxml = prjmeta.find(flag)
        if not flagxml:  # appending is fine
            flagxml = ET.SubElement(prjmeta, flag)

        foundone = False
        for build in flagxml:
            if build.get('repository', None) == repository and build.get('arch', None) == arch:
                build.tag = state
                foundone = True

        # need to add a global one
        if not foundone:
            query = {}
            if arch:
                query['arch'] = arch
            if repository:
                query['repository'] = repository
            ET.SubElement(flagxml, state, query)

        http_PUT(url, data=ET.tostring(prjmeta))

    def build_switch_prj(self, project, state):
        """
        Switch build state of project to desired state
        :param project: project to switch state for
        :param state: desired state for build
        """
        self.switch_flag_in_prj(project, flag='build', state=state, repository=None, arch=None)

    def prj_frozen_enough(self, project):
        """
        Check if we can and should refreeze the prj"
        :param project the project to check
        :returns True if we can select into it
        """

        data = self.get_prj_pseudometa(project)
        if data['requests']:
            return True  # already has content

        # young enough
        if self.days_since_last_freeze(project) < 6.5:
            return True

        return False

    def build_switch_staging_project(self, target_project):
        """
        Verify what packages are in project and switch the build
        accordingly.
        :param target_project: project we validate and switch
        """
        meta = self.get_prj_pseudometa(target_project)
        staged_requests = list()
        for request in meta['requests']:
            staged_requests.append(request['id'])
        target_flag = 'disable'
        # for adi projects we always build
        if self.is_adi_project(target_project):
            target_flag = 'enable'
        elif not self.crings or self.check_ring_packages(target_project, staged_requests):
            target_flag = 'enable'
        self.build_switch_prj(target_project, target_flag)

        if self.item_exists(target_project + ":DVD"):
            self.build_switch_prj(target_project + ":DVD", target_flag)

    def item_exists(self, project, package=None):
        """
        Return true if the given project exists
        :param project: project name to check
        :param package: optional package to check
        """
        if package:
            url = self.makeurl(['source', project, package, '_meta'])
        else:
            url = self.makeurl(['source', project, '_meta'])
        try:
            http_GET(url)
        except urllib2.HTTPError:
            return False
        return True

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

        specfile = self.load_file_content(project, package, '{}.spec'.format(package))
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
        except urllib2.HTTPError, e:
            if e.code == 404:
                return None
            raise

    def load_file_content(self, project, package, filename):
        """
        Load the content of a file and return the content as data. If the package is a link, it will be expanded
        :param project: The project to query
        :param package:  The package to quert
        :param filename: The filename to query
        """
        url = self.makeurl(['source', project, package, '{}?expand=1'.format(filename)])
        try:
            return http_GET(url).read()
        except urllib2.HTTPError:
            return None

    def save_file_content(self, project, package, filename, content):
        """
        Save content to a project/package/file
        :param project: The project containing the package
        :param package: the package to update
        :param filename: the filename to save the data to
        :param content: the content to write to the file
        """
        url = self.makeurl(['source', project, package, filename])
        http_PUT(url + '?comment=scripted+update', data=content)

    def update_status_comments(self, project, command):
        """
        Refresh the status comments, used for notification purposes, based on
        the current list of requests. To ensure that all involved users
        (and nobody else) get notified, old status comments are deleted and
        a new one is created.
        :param project: project name
        :param command: name of the command to include in the message
        """

        # TODO: we need to discuss the best way to keep track of status
        # comments. Right now they are marked with an initial markdown
        # comment. Maybe a cleaner approach would be to store something
        # like 'last_status_comment_id' in the pseudometa. But the current
        # OBS API for adding comments doesn't return the id of the created
        # comment.

        comment_api = CommentAPI(self.apiurl)

        comments = comment_api.get_comments(project_name=project)
        for comment in comments.values():
            # TODO: update the comment removing the user mentions instead of
            # deleting the whole comment. But there is currently not call in
            # OBS API to update a comment
            if comment['comment'].startswith('<!--- osc staging'):
                comment_api.delete(comment['id'])
                break  # There can be only one! (if we keep deleting them)

        meta = self.get_prj_pseudometa(project)
        lines = ['<!--- osc staging %s --->' % command]
        lines.append('The list of requests tracked in %s has changed:\n' % project)
        for req in meta['requests']:
            author = req.get('autor', None)
            if not author:
                # Old style metadata
                author = get_request(self.apiurl, str(req['id'])).get_creator()
            lines.append('  * Request#%s for package %s submitted by @%s' % (req['id'], req['package'], author))
        msg = '\n'.join(lines)
        comment_api.add_comment(project_name=project, comment=msg)

    def mark_additional_packages(self, project, packages):
        """
        Adds packages that the repo checker needs to download from staging prj
        """
        meta = self.get_prj_pseudometa(project)
        additionals = set(meta.get('add_to_repo', []))
        additionals.update(packages)
        meta['add_to_repo'] = sorted(additionals)
        self.set_prj_pseudometa(project, meta)

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

    def check_pkgs(self, rebuild_list):
        return list(set(rebuild_list) & set(self.list_packages(self.project)))

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
            print "tried to trigger rebuild for project '%s' package '%s'" % (prj, pkg)
            http_POST(u)
        except:
            print "could not trigger rebuild for project '%s' package '%s'" % (prj, pkg)


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

    def create_adi_project(self, name):
        """Create an ADI project."""
        if not name:
            name = self._candidate_adi_project()
        else:
            name = self.adi_prj_from_number(name)

        adi_projects = self.get_adi_projects()
        if name in adi_projects:
            raise Exception('Project {} already exist'.format(name))

        meta = """
        <project name="{}">
          <title></title>
          <description></description>
          <publish>
            <disable/>
          </publish>
          <debuginfo>
            <enable/>
          </debuginfo>
          <repository name="standard">
            <path project="{}" repository="standard"/>
            <arch>x86_64</arch>
          </repository>
        </project>""".format(name, self.project)
        url = make_meta_url('prj', name, self.apiurl)
        http_PUT(url, data=meta)
        # put twice because on first put, the API adds useless maintainer
        http_PUT(url, data=meta)

        return name

    def is_user_member_of(self, user, group):
        root = ET.fromstring(get_group(self.apiurl, group))

        if root.findall("./person/person[@userid='%s']" % user):
            return True
        else:
            return False

    # from manager_42
    def _fill_package_meta(self, project):
        url = makeurl(self.apiurl, ['search', 'package'], "match=[@project='%s']" % project)
        root = ET.parse(self.retried_GET(url))
        for p in root.findall('package'):
            name = p.attrib['name']
            self._package_metas.setdefault(project, {})[name] = p

    def get_devel_project(self, project, package):
        if not project in self._package_metas:
            self._fill_package_meta(project)

        if not package in self._package_metas[project]:
            return None

        node = self._package_metas[project][package].find('devel')
        if node is None:
            return None

        return node.get('project')
