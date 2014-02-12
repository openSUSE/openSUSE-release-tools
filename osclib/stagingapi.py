# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

import logging

from osc.core import *
import yaml


class StagingApi(object):
    """
    Class containing various api calls to work with staging projects.
    """

    rings = ['openSUSE:Factory:Rings:0-Bootstrap',
             'openSUSE:Factory:Rings:1-MinimalX']
    ring_packages = dict()
    apiurl = ""

    def __init__(self, apiurl):
        """
        Initialize global variables
        """

        self.apiurl = apiurl
        self.ring_packages = self._generate_ring_packages()


    def _generate_ring_packages(self):
        """
        Generate dictionary with names of the rings
        :return dictionary with ring names
        """

        ret = dict()

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

        package_info = dict()

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
        link_pac(package_info['project'], package_info['package'], destination_project, package, force=True, rev=package_info['srcmd5'])

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

        url = makeurl(self.apiurl, ['search', 'project', 'id?match=starts-with(@name,\'openSUSE:Factory:Staging:\')'])
        projxml = http_GET(url)

        root = ET.parse(projxml).getroot()
        for val in root.findall('project'):
            projects.append(val.get('name'))
        return projects


    def staging_change_review_state(self, id, newstate, message):
        """
        Change review state of the staging request
        :param id: id of the request
        :param newstate: state of the new request
        :param message: message for the review
        """
        """ taken from osc/osc/core.py, improved:
            - verbose option added,
            - empty by_user=& removed.
            - numeric id can be int().
        """
        query = {'cmd': 'changereviewstate',
                 'newstate': newstate,
                 'by_group': 'factory-staging',
                 'comment': message}

        url = makeurl(self.apiurl, ['request', str(id)], query=query)
        f = http_POST(url, data=message)

    def accept_non_ring_request(self, request):
        """
        Accept review of requests that are not yet in
        any ring so we don't delay their testing.
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
            logging.info('no target/package in request {0}, action {1}; '.format(id, action))

        # Verify the package ring
        ring = self.ring_packages.get(target_package, None)
        # DVD and main desktops are ignored for now
        if ring is None:
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


    def sr_to_prj(self, request_id, project):
        """
        Links sources from request to project
        :param request_id: request to link
        :param project: project to link into
        """

        # read info from sr
        req = get_request(self.apiurl, request_id)
        if not req:
            raise oscerr.WrongArgs("Request {0} not found".format(request_id))
        act = req.get_actions("submit")
        if not act:
            raise oscerr.WrongArgs("Request {0} is not a submit request".format(request_id))
        act=act[0]

        src_prj = act.src_project
        src_rev = act.src_rev
        src_pkg = act.src_package
        tar_pkg = act.tgt_package

        # expand the revision to a md5
        url =  makeurl(self.apiurl, ['source', src_prj, src_pkg], { 'rev': src_rev, 'expand': 1 })
        f = http_GET(url)
        root = ET.parse(f).getroot()
        src_rev =  root.attrib['srcmd5']
        src_vrev = root.attrib['vrev']
        #print "osc linkpac -r %s %s/%s %s/%s" % (src_rev, src_prj, src_pkg, project, tar_pkg)

        # link stuff
        self._add_rq_to_prj_pseudometa(project, int(request_id), src_pkg)
        link_pac(src_prj, src_pkg, project, tar_pkg, force=True, rev=src_rev, vrev=src_vrev)
        # FIXME If there are links in parent project, make sure that current
