# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

import logging
from xml.etree import cElementTree as ET

import yaml
import re
import urllib2

from osc import oscerr
from osc.core import change_review_state
from osc.core import delete_package
from osc.core import get_request
from osc.core import make_meta_url
from osc.core import makeurl
from osc.core import metafile
from osc.core import http_GET
from osc.core import http_POST
from osc.core import http_PUT


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

    def makeurl(self, l, query=None):
        """
        Wrapper around osc's makeurl passing our apiurl
        :return url made for l and query
        """
        query = [] if not query else query
        return makeurl(self.apiurl, l, query)

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

        url = self.makeurl(['source', project, pkgname])
        content = http_GET(url)
        root = ET.parse(content).getroot().find('linkinfo')
        package_info['srcmd5'] = root.attrib['srcmd5']
        package_info['rev'] = root.attrib['rev']
        package_info['project'] = root.attrib['project']
        package_info['package'] = root.attrib['package']
        return package_info

    def move_between_project(self, source_project, req_id, destination_project):
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

    def get_staging_projects(self):
        """
        Get all current running staging projects
        :return list of known staging projects
        """

        projects = []

        url = self.makeurl(['search', 'project',
                            'id?match=starts-with(@name,\'openSUSE:Factory:Staging:\')'])
        projxml = http_GET(url)
        root = ET.parse(projxml).getroot()
        for val in root.findall('project'):
            projects.append(val.get('name'))
        return projects

    def change_review_state(self, request_id, newstate, message=None, by_group=None,
                            by_user=None, by_project=None):
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
                return change_review_state(self.apiurl, str(request_id), newstate,
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
            raise oscerr.WrongArgs('Request {} has no action'.format(request_id))
        # we care only about first action
        action = action[0]

        # Where are we targeting the package
        target_project = action.find('target').get('project')
        target_package = action.find('target').get('package')

        # If the values are empty it is no error
        if not target_project or not target_package:
            logging.info('no target/package in request {}, action {}; '.format(request_id, action))

        # Verify the package ring
        ring = self.ring_packages.get(target_package, None)
        if not ring:
            # accept the request here
            message = "No need for staging, not in tested ring project."
            self.change_review_state(request_id, 'accepted', message=message, by_group='factory-staging')

    def get_open_requests(self):
        """
        Get all requests with open review for staging project
        that are not yet included in any staging project
        :return list of pending open review requests
        """

        requests = []

        # xpath query, using the -m, -r, -s options
        where = "@by_group='factory-staging'+and+@state='new'"

        url = self.makeurl(['search', 'request'], "match=state/@name='review'+and+review[%s]" % where)
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
        f = http_GET(url)
        root = ET.parse(f).getroot()
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
        nt = ', '.join(sorted(new_title))
        title.text = nt[:240]
        # Write XML back
        url = make_meta_url('prj', project, self.apiurl, force=True)
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
            data['requests'].append({'id': request_id, 'package': package})
        self.set_prj_pseudometa(project, data)
        # FIXME Add sr to group request as well

    def get_request_id_for_package(self, project, package):
        """
        Query the request id from meta
        :param project: project to remove from
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
        :param project: project to remove from
        :param package: package we want to query for
        """
        data = self.get_prj_pseudometa(project)
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
        # FIXME Add sr to group request as well

    def rm_from_prj(self, project, package=None, request_id=None, msg=None, review='accepted'):
        """
        Delete request from the project
        :param project: project to remove from
        :param request_id: request we want to remove
        :param msg: message for the log
        """

        if package:
            request_id = self.get_request_id_for_package(project, package)
            if not request_id:  # already gone?
                return
        else:
            package = self.get_package_for_request_id(project, request_id)
            if not package:  # already gone?
                return

        self._remove_package_from_prj_pseudometa(project, package)
        delete_package(self.apiurl, project, package, force=True, msg=msg)
        self.set_review(request_id, project, state=review)

    def create_package_container(self, project, package, disable_build=False):
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

        url = self.makeurl(['source', project, package, '_meta'])
        http_PUT(url, data=dst_meta)

    def check_one_request(self, request, project):
        """
        Check if a staging request is ready to be approved. Reviews for the project
        are ignored, other open reviews will block the acceptance
        :param project: staging project
        :param request_id: request id to check
        """

        f = http_GET(self.makeurl(['request', str(request)]))
        root = ET.parse(f).getroot()

        # relevant info for printing
        package = str(root.find('action').find('target').attrib['package'])

        state = root.find('state').get('name')
        if state in ['declined', 'superseded', 'revoked']:
            return '{}: {}'.format(package, state)

        # instead of just printing the state of the whole request find out who is
        # remaining on the review and print it out, otherwise print out that it is
        # ready for approval and waiting on others from GR to be accepted
        review_state = root.findall('review')
        failing_groups = []
        for i in review_state:
            if i.attrib['state'] == 'accepted':
                continue
            if i.get('by_project', None) == project:
                continue
            for attrib in ['by_group', 'by_user', 'by_project', 'by_package']:
                value = i.get(attrib, None)
                if value:
                    failing_groups.append(value)

        if not failing_groups:
            return None
        else:
            state = 'missing reviews: ' + ', '.join(failing_groups)
            return '{}: {}'.format(package, state)

    def check_project_status(self, project, verbose=False):
        """
        Checks a staging project for acceptance. Checks all open requests for open reviews
        and build status
        :param project: project to check
        :return true (ok)/false (empty prj) or list of strings with informations)
        """

        # Report
        report = list()

        # all requests with open review
        requests = self.list_requests_in_prj(project)
        open_requests = set(requests)

        # all tracked requests - some of them might be declined, so we don't see them above
        meta = self.get_prj_pseudometa(project)
        for req in meta['requests']:
            req = req['id']
            if req in open_requests:
                open_requests.remove(req)
            if req not in requests:
                requests.append(req)
        if len(open_requests) != 0:
            return ['Request(s) {} are not tracked but are open for the prj'.format(','.join(open_requests))]

        # If we find no requests in staging then it is empty so we ignore it
        if len(requests) == 0:
            return False

        # Check if the requests are acceptable and bail out on
        # first failure unless verbose as it is slow
        for request in requests:
            ret = self.check_one_request(request, project)
            if ret:
                report.append(ret)
                if not verbose:
                    break

        # Check the buildstatus
        buildstatus = self.gather_build_status(project)
        if buildstatus:
            # here no append as we are adding list to list
            report += self.generate_build_status_details(buildstatus, verbose)

        # Check the openqa state
        ret = self.find_openqa_state(project)
        if ret:
            report.append(ret)

        if report:
            return report
        else:
            # The only case we are green
            return True

    def find_openqa_state(self, project):
        """
        Checks the openqa state of the project
        :param project: project to check
        :return None or list with issue informations
        """
        u = self.makeurl(['build', project, 'images', 'x86_64', 'Test-DVD-x86_64'])
        f = http_GET(u)
        root = ET.parse(f).getroot()

        filename = None
        for binary in root.findall('binary'):
            filename = binary.get('filename', '')
            if filename.endswith('.iso'):
                break

        if not filename:
            return 'No ISO built in {}'.format(u)

        # don't look here - we will replace that once we have OBS<->openQA sync
        baseurl = 'http://opensuseqa.suse.de/openqa/testresults/openSUSE-Factory-staging'
        url = baseurl + '_' + project.split(':')[-1].lower() + '-x86_64-Build'
        result = re.match('Test-([\d\.]+)-Build(\d+)\.(\d+)-Media.iso', filename)
        url += result.group(1)
        bn = int(result.group(2)) * 100 + int(result.group(3))
        url += '.{}'.format(bn)
        url += "-minimalx/results.json"

        try:
            f = urllib2.urlopen(url)
        except urllib2.HTTPError:
            return 'No openQA result (yet) for {}'.format(url)

        import json
        openqa = json.load(f)
        overall = openqa.get('overall', 'inprogress')
        if overall != 'ok':
            return "Openqa's overall status is {}".format(overall)

        for module in openqa['testmodules']:
            # zypper_in fails at the moment - urgent fix needed
            if module['result'] != 'ok' and module['name'] not in []:
                return "{} test failed".format(module['name'])

        return None

    def gather_build_status(self, project):
        """
        Checks whether everything is built in project
        :param project: project to check
        """
        # Get build results
        u = self.makeurl(['build', project, '_result'])
        f = http_GET(u)
        root = ET.parse(f).getroot()

        # Check them
        broken = []
        working = []
        # Iterate through repositories
        for results in root.findall('result'):
            building = False
            if results.get('state') not in ('published', 'unpublished') or results.get('dirty') == 'true':
                working.append({
                    'path': '{}/{}'.format(results.get('repository'), results.get('arch')),
                    'state': results.get('state')
                })
                building = True
            # Iterate through packages
            for node in results:
                # Find broken
                result = node.get('code')
                if result in ['broken', 'failed'] or (result == 'unresolvable' and not building):
                    broken.append({
                        'pkg': node.get('package'),
                        'state': result,
                        'path': '{}/{}'.format(results.get('repository'), results.get('arch'))
                    })

        # Print the results
        if len(working) == 0 and len(broken) == 0:
            return None
        else:
            return [project, working, broken]

    def generate_build_status_details(self, details, verbose=False):
        """
        Generate list of strings for the buildstatus detail report.
        :param details: buildstatus informations about project
        :return list of strings for printing
        """

        retval = list()
        if not isinstance(details, list):
            return retval
        project, working, broken = details

        if len(working) != 0:
            retval.append('At least following repositories is still building:')
            for i in working:
                retval.append('    {}: {}'.format(i['path'], i['state']))
                if not verbose:
                    break
        if len(broken) != 0:
            retval.append('Following packages are broken:')
            for i in broken:
                retval.append('    {} ({}): {}'.format(i['pkg'], i['path'], i['state']))

        return retval

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
            raise oscerr.WrongArgs('Request {} is not a submit or delete request'.format(request_id))

        # register the package name
        self._add_rq_to_prj_pseudometa(project, int(request_id), tar_pkg)

        # add review
        self.add_review(request_id, project)

        # now remove the staging checker
        self.change_review_state(request_id, 'accepted',
                                 by_group='factory-staging',
                                 message='Picked {}'.format(project))

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
        url = self.makeurl(['build', project], {'cmd': 'wipe', 'package': tar_pkg})
        http_POST(url)

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

        disable_build = False
        if not self.ring_packages.get(tar_pkg):
            disable_build = True
        self.create_package_container(project, tar_pkg, disable_build=disable_build)

        # expand the revision to a md5
        url = self.makeurl(['source', src_prj, src_pkg], {'rev': src_rev, 'expand': 1})
        f = http_GET(url)
        root = ET.parse(f).getroot()
        src_rev = root.attrib['srcmd5']
        src_vrev = root.attrib.get('vrev')

        # link stuff - not using linkpac because linkpac copies meta from source
        root = ET.Element('link', package=src_pkg, project=src_prj, rev=src_rev)
        if src_vrev:
            root.attrib['vrev'] = src_vrev
        url = self.makeurl(['source', project, tar_pkg, '_link'])
        http_PUT(url, data=ET.tostring(root))
        return tar_pkg

    def prj_from_letter(self, letter):
        if ':' in letter:  # not a letter
            return letter
        return 'openSUSE:Factory:Staging:%s' % letter

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

        query = {}
        if by_project:
            query['by_project'] = by_project
            if not msg:
                msg = 'Being evaluated by staging project "{}"'.format(by_project)
        if by_group:
            query['by_group'] = by_group
            if not msg:
                msg = 'Being evaluated by group "{}"'.format(by_group)
        if len(query) == 0:
            raise oscerr.WrongArgs('We need a group or a project')
        query['cmd'] = 'addreview'
        url = self.makeurl(['request', str(request_id)], query)
        http_POST(url, data=msg)

    def set_review(self, request_id, project, state='accepted'):
        """
        Sets review for request done by project
        :param request_id: request to change review for
        :param project: project to do the review
        """
        req = get_request(self.apiurl, str(request_id))
        if not req:
            raise oscerr.WrongArgs('Request {} not found'.format(request_id))
        cont = False
        for i in req.reviews:
            if i.by_project == project and i.state == 'new':
                cont = True
        if cont:
            msg = 'Reviewed by staging project "{}" with result: "{}"'.format(project, state)
            self.change_review_state(request_id, state, by_project=project,
                                     message=msg)

    def build_switch_prj(self, prj, state):
        url = self.makeurl(['source', prj, '_meta'])
        prjmeta = ET.parse(http_GET(url)).getroot()

        foundone = False
        for f in prjmeta.find('build'):
            if not f.get('repository', None) and not f.get('arch', None):
                f.tag = state
                foundone = True

        # need to add a global one
        if not foundone:
            ET.SubElement(prjmeta.find('build'), state)

        http_PUT(url, data=ET.tostring(prjmeta))
