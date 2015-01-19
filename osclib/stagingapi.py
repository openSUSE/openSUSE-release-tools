# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

import json
import logging
import urllib2
import time
from xml.etree import cElementTree as ET

import yaml

from osc import oscerr
from osc.core import change_review_state
from osc.core import delete_package
from osc.core import get_request
from osc.core import make_meta_url
from osc.core import makeurl
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT

from osclib.comments import CommentAPI


class StagingAPI(object):
    """
    Class containing various api calls to work with staging projects.
    """

    def __init__(self, apiurl, opensuse='Factory'):
        """
        Initialize instance variables
        """

        self.apiurl = apiurl
        self.opensuse = opensuse
        self.rings = (
            'openSUSE:{}:Rings:0-Bootstrap'.format(self.opensuse),
            'openSUSE:{}:Rings:1-MinimalX'.format(self.opensuse),
            'openSUSE:{}:Rings:2-TestDVD'.format(self.opensuse)
        )
        self.ring_packages = self._generate_ring_packages()
        self.packages_staged = self._get_staged_requests()

    def makeurl(self, l, query=None):
        """
        Wrapper around osc's makeurl passing our apiurl
        :return url made for l and query
        """
        query = [] if not query else query
        return makeurl(self.apiurl, l, query)

    def retried_GET(self, url):
        try:
            return http_GET(url)
        except urllib2.HTTPError, e:
            if e.code / 100 == 5:
                print 'Retrying {}'.format(url)
                return self.retried_GET(url)
            raise e

    def retried_POST(self, url):
        try:
            return http_POST(url)
        except urllib2.HTTPError, e:
            if e.code == 504:
                print 'Timeout on {}'.format(url)
                return '<status code="timeout"/>'
            if e.code / 100 == 5:
                print 'Retrying {}'.format(url)
                return self.retried_POST(url)
            raise e

    def retried_PUT(self, url, data):
        try:
            return http_PUT(url, data=data)
        except urllib2.HTTPError, e:
            if e.code / 100 == 5:
                print 'Retrying {}'.format(url)
                return self.retried_PUT(url, data)
            raise e

    def _generate_ring_packages(self):
        """
        Generate dictionary with names of the rings
        :return dictionary with ring names
        """

        ret = {}

        for prj in self.rings:
            url = self.makeurl(['source', prj])
            root = http_GET(url)
            for entry in ET.parse(root).getroot().findall('entry'):
                pkg = entry.attrib['name']
                if pkg in ret and pkg != 'Test-DVD-x86_64':
                    msg = '{} is defined in two projects ({} and {})'
                    raise Exception(msg.format(pkg, ret[pkg], prj))
                ret[pkg] = prj
        return ret

    def _get_staged_requests(self):
        """
        Get all requests that are already staged
        :return dict of staged requests with their project and srid
        """

        packages_staged = dict()
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

        query = "id?match=starts-with(@name,'openSUSE:{}:Staging:')".format(self.opensuse)
        url = self.makeurl(['search', 'project', query])
        projxml = http_GET(url)
        root = ET.parse(projxml).getroot()
        for val in root.findall('project'):
            projects.append(val.get('name'))
        return projects

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
                                        by_group='factory-staging')

    def supseded_request(self, request):
        """
        Returns a staging info for a request or None
        :param request - a Request instance
        :return dict with 'prj' and 'rq_id' of the old request
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

        # If the package is currently tracked then we do the replacement
        stage_info = self.packages_staged.get(target_package, {'prj': '', 'rq_id': 0})
        if stage_info['rq_id'] != 0 and int(stage_info['rq_id']) != request_id:
            return stage_info
        return None

    def update_superseded_request(self, request):
        """
        Replace superseded requests that are already in some
        staging prj
        :param request: request we are checking if it is fine
        """

        stage_info = self.supseded_request(request)
        request_id = int(request.get('id'))

        if stage_info:
            # Remove the old request
            self.rm_from_prj(stage_info['prj'],
                             request_id=stage_info['rq_id'],
                             msg='Replaced by newer request',
                             review='declined')
            # Add the new one that should be replacing it
            self.rq_to_prj(request_id, stage_info['prj'])

    def get_open_requests(self):
        """
        Get all requests with open review for staging project
        that are not yet included in any staging project
        :return list of pending open review requests
        """

        requests = []

        # xpath query, using the -m, -r, -s options
        where = "@by_group='factory-staging'+and+@state='new'"
        target = "@project='openSUSE:{}'".format(self.opensuse)
        target_nf = "@project='openSUSE:{}:NonFree'".format(self.opensuse)

        query = "match=state/@name='review'+and+review[{}]+and+(target[{}]+or+target[{}])".format(
            where, target, target_nf)
        url = self.makeurl(['search', 'request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for rq in root.findall('request'):
            requests.append(rq)
        return requests

    def dispatch_open_requests(self):
        """
        Verify all requests and dispatch them to staging projects or
        approve them

        """

        # get all current pending requests
        requests = self.get_open_requests()
        # check if we can reduce it down by accepting some
        for rq in requests:
            self.accept_non_ring_request(rq)
            self.update_superseded_request(rq)

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
        _prefix = 'openSUSE:{}:Staging:'.format(self.opensuse)
        if project.startswith(_prefix):
            project = project.replace(_prefix, '')

        query = {'format': 'json'}
        url = self.makeurl(('project',  'staging_projects', 'openSUSE:%s' % self.opensuse, project),
                           query=query)
        result = json.load(self.retried_GET(url))
        return result['overall_state'] == 'acceptable'

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

    def check_if_job_is_ok(self, job):
        url = 'https://openqa.opensuse.org/tests/{}/file/results.json'.format(job['id'])
        try:
            f = urllib2.urlopen(url)
        except urllib2.HTTPError:
            return "Can't open {}".format(url)

        try:
            openqa = json.load(f)
        except ValueError:
            return "Can't decode {}".format(url)

        overall = openqa.get('overall', 'inprogress')
        if job['test'] == 'miniuefi':
            return None  # ignore
        # pprint.pprint(openqa)
        # pprint.pprint(job)
        if overall != 'ok':
            return "openQA's overall status is {} for https://openqa.opensuse.org/tests/{}".format(overall, job['id'])

        for module in openqa['testmodules']:
            # zypper_in fails at the moment - urgent fix needed
            if module['result'] == 'ok':
                continue
            if module['name'] in ['kate', 'ooffice', 'amarok', 'thunderbird', 'gnucash']:
                continue
            return '{} test failed: https://openqa.opensuse.org/tests/{}'.format(module['name'], job['id'])
        return None

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
                                    by_group='factory-staging',
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

        ring_dvd = 'openSUSE:{}:Rings:2-TestDVD'.format(self.opensuse)
        if self.ring_packages.get(pkg) == ring_dvd:
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
            if not self.ring_packages.get(tar_pkg):
                disable_build = True
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
        return 'openSUSE:{}:Staging:{}'.format(self.opensuse, letter)

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
        if self.check_ring_packages(target_project, staged_requests):
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
