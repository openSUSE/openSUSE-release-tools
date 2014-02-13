# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

import logging
from xml.etree import cElementTree as ET

import yaml

from osc import oscerr
from osc.core import delete_package
from osc.core import get_request
from osc.core import make_meta_url
from osc.core import makeurl
from osc.core import metafile
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT
from osc.core import link_pac


class StagingAPI(object):
    """
    Class containing various api calls to work with staging projects.
    """

    def __init__(self, apiurl):
        """
        Initialize instance variables
        """

        self.apiurl = apiurl
        self.rings = ['openSUSE:Factory:Rings:0-Bootstrap',
                      'openSUSE:Factory:Rings:1-MinimalX']
        self.ring_packages = self._generate_ring_packages()


    def _generate_ring_packages(self):
        """
        Generate dictionary with names of the rings
        :return dictionary with ring names
        """

        ret = {}

        for prj in self.rings:
            url = makeurl(self.apiurl, ['source', prj])
            root = http_GET(url)
            for entry in ET.parse(root).getroot().findall('entry'):
                ret[entry.attrib['name']] = prj
        return ret


    def get_package_information(self, project, pkgname):
        """
        Get the revision packagename and source project to copy from
        based on content provided
        :param project: the project we are having the package in
        :param pkgname: name of the package we want to identify
        :return dict ( project, package, revision, md5sum )
        """

        package_info = {}

        url =  makeurl(self.apiurl, ['source', project, pkgname])
        content = http_GET(url)
        root = ET.parse(content).getroot().find('linkinfo')
        package_info['srcmd5'] =  root.attrib['srcmd5']
        package_info['rev'] = root.attrib['rev']
        package_info['project'] = root.attrib['project']
        package_info['package'] = root.attrib['package']

        return package_info


    def move_between_project(self, source_project, package, destination_project):
        """
        Move selected package from one staging to another
        """

        # Get the relevant information from source
        package_info = self.get_package_information('source_project', 'package')

        # Copy the package
        #FIXME: add the data from orginal project yaml to the destination one
        link_pac(package_info['project'],
                 package_info['package'],
                 destination_project,
                 package,
                 force=True,
                 rev=package_info['srcmd5'])

        # Delete the first location
        message = 'moved to {0}'.format(destination_project)
        delete_package(self.apiurl, source_project, package, msg=message)
        #FIXME: delete the data from YAML


    def get_staging_projects(self):
        """
        Get all current running staging projects
        :return list of known staging projects
        """

        projects = []

        url = makeurl(self.apiurl, ['search', 'project',
                                    'id?match=starts-with(@name,\'openSUSE:Factory:Staging:\')'])
        projxml = http_GET(url)
        root = ET.parse(projxml).getroot()
        for val in root.findall('project'):
            projects.append(val.get('name'))
        return projects


    def staging_change_review_state(self, request_id, newstate, message):
        """
        Change review state of the staging request
        :param request_id: id of the request
        :param newstate: state of the new request
        :param message: message for the review
        """
        """ taken from osc/osc/core.py, improved:
            - verbose option added,
            - empty by_user=& removed.
            - numeric id can be int().
        """
        query = {
            'cmd': 'changereviewstate',
            'newstate': newstate,
            'by_group': 'factory-staging',
            'comment': message
        }

        url = makeurl(self.apiurl, ['request', str(request_id)], query=query)
        http_POST(url, data=message)


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
            raise oscerr.WrongArgs('Request {0} has no action'.format(request_id))
        # we care only about first action
        action = action[0]

        # Where are we targeting the package
        target_project = action.find('target').get('project')
        target_package = action.find('target').get('package')

        # If the values are empty it is no error
        if not target_project or not target_package:
            logging.info('no target/package in request {0}, action {1}; '.format(request_id, action))

        # Verify the package ring
        ring = self.ring_packages.get(target_package, None)
        if not ring:
            # accept the request here
            message = "No need for staging, not in tested ring project."
            self.staging_change_review_state(request_id, 'accepted', message)


    def get_open_requests(self):
        """
        Get all requests with open review for staging project
        that are not yet included in any staging project
        :return list of pending open review requests
        """

        requests = []

        # xpath query, using the -m, -r, -s options
        where = "@by_group='factory-staging'+and+@state='new'"

        url = makeurl(self.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()

        for rq in root.findall('request'):
            requests.append(rq)

        return requests


    def dispatch_open_requests(self):
        """
        Verify all requests and dispatch them to staging projects or approve them
        """

        # get all current pending requests
        requests = self.get_open_requests()
        # check if we can reduce it down by accepting some
        for rq in requests:
            self.accept_non_ring_request(rq)

        # FIXME: dispatch to various staging projects automatically


    def get_prj_pseudometa(self, project):
        """
        Gets project data from YAML in project description
        :param project: project to read data from
        :return structured object with metadata
        """

        url = make_meta_url('prj', project, self.apiurl)
        data = http_GET(url).readlines()
        root = ET.fromstring(''.join(data))
        description = root.find('description')
        # If YAML parsing fails, load default
        # FIXME: Better handling of errors
        # * broken description
        # * directly linked packages
        # * removed linked packages
        try:
            data = yaml.load(description.text)
            data['requests']
        except:
            data = yaml.load('requests: []')
        return data


    def set_prj_pseudometa(self, project, meta):
        """
        Sets project description to the YAML of the provided object
        :param project: project to save into
        :param meta: data to save
        """

        # Get current metadata
        url = make_meta_url('prj', project, self.apiurl)
        data = http_GET(url).readlines()
        root = ET.fromstring(''.join(data))
        # Find description
        description = root.find('description')
        # Replace it with yaml
        description.text = yaml.dump(meta)
        # Find title
        title = root.find('title')
        # Put something nice into title as well
        new_title = []
        for request in meta['requests']:
            new_title.append(request['package'])
        title.text = ', '.join(new_title)
        # Write XML back
        url = make_meta_url('prj',project, self.apiurl, force=True)
        f = metafile(url, ET.tostring(root))
        http_PUT(f.url, file=f.filename)


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
                request['id'] = request_id
                append = False
        if append:
            data['requests'].append( { 'id': request_id, 'package': package} )
        self.set_prj_pseudometa(project, data)
        # FIXME Add sr to group request as well


    def _remove_rq_from_prj_pseudometa(self, project, package):
        """
        Delete request from the project pseudometa
        :param project: project to remove from
        :param package: package we want to remove from meta
        """

        data = self.get_prj_pseudometa(project)
        requests = data['requests']
        data['requests'] = list()

        for request in requests:
            if not request['package'] == package:
                newdata['requests'].append( { 'id': request['id'], 'package': request['package']} )
        self.set_prj_pseudometa(project, newdata)
        # FIXME Add sr to group request as well


    def create_package_container(self, project, package, disable_build = False):
        """
        Creates a package container without any fields in project/package
        :param project: project to create it
        :param package: package name
        :param disable_build: should the package be created with build flag disabled
        """
        dst_meta = '<package name="%s"><title/><description/></package>' % package
        if disable_build:
            root = ET.fromstring(dst_meta)
            elm = ET.SubElement(root, 'build')
            ET.SubElement(elm, 'disable')
            dst_meta = ET.tostring(root)

        url = makeurl(self.apiurl, ['source', project, package, '_meta'] )
        http_PUT(url, data=dst_meta)

    def rq_to_prj(self, request_id, project):
        """
        Links request to project - delete or submit
        :param request_id: request to link
        :param project: project to link into
        """
        # read info from sr
        tar_pkg = None

        req = get_request(self.apiurl, request_id)
        if not req:
            raise oscerr.WrongArgs("Request {0} not found".format(request_id))

        act = req.get_actions("submit")
        if act:
            tar_pkg = self.sr_to_prj(act[0], project)

        act = req.get_actions("delete")
        if act:
            tar_pkg = self.delete_to_prj(act[0], project)

        if not tar_pkg:
            raise oscerr.WrongArgs("Request {0} is not a submit or delete request".format(request_id))

        # register the package name
        self._add_rq_to_prj_pseudometa(project, int(request_id), tar_pkg)

    def delete_to_prj(self, act, project):
        """
        Hides Package in project
        :param act: action for delete request
        :param project: project to hide in
        """

        tar_pkg = act.tgt_package

        # create build disabled package
        self.create_package_container(project, tar_pkg, disable_build=True)
        # now trigger wipebinaries to emulate a delete
        url =  makeurl(self.apiurl, ['build', project], { 'cmd': 'wipe', 'package': tar_pkg  } )
        http_POST(url)

        return tar_pkg

    def sr_to_prj(self, act, project):
        """
        Links sources from request to project
        :param act: action for submit request
        :param project: project to link into
        """

        src_prj = act.src_project
        src_rev = act.src_rev
        src_pkg = act.src_package
        tar_pkg = act.tgt_package

        self.create_package_container(project, tar_pkg)

        # expand the revision to a md5
        url =  makeurl(self.apiurl, ['source', src_prj, src_pkg], { 'rev': src_rev, 'expand': 1 })
        f = http_GET(url)
        root = ET.parse(f).getroot()
        src_rev =  root.attrib['srcmd5']
        src_vrev = root.attrib['vrev']

        # link stuff - not using linkpac because linkpac copies meta from source
        root = ET.Element('link', package=src_pkg, project=src_prj, rev=src_rev, vrev=src_vrev)
        url = makeurl(self.apiurl, ['source', project, tar_pkg, '_link'])
        http_PUT(url, data=ET.tostring(root))
        return tar_pkg
