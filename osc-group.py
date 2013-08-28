#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# (C) 2013 coolo@suse.de, openSUSE.org
# (C) 2013 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

import osc
import osc.core

from osc import cmdln
from osc import conf

OSC_GROUP_VERSION='0.0.3'

def _print_version(self):
    """ Print version information about this extension. """

    print('%s' % self.OSC_GROUP_VERSION)
    quit(0)

def _group_find_request_id(self, submit_request, opts):
    """
    Look up the submit_request by ID to verify if it is correct
    :param submit_request: ID of the added request
    :param opts: obs options
    """

    url = makeurl(opts.apiurl, ['request'], 'states=new,review&project=openSUSE:Factory&view=collection')
    f = http_GET(url)
    root = ET.parse(f).getroot()
    
    res = []
    for rq in root.findall('request'):
        res.append(int(rq.attrib['id']))
    
    # we have various stuff passed, and it might or might not be int we need for the comparison
    try:
        i = int(submit_request)
    except ValueError:
        return None

    if i in res:
        return submit_request
    else:
        # raise oscerr.ServiceRuntimeError('There is no request for SR#{0}'.format(submit_request))
        return None

def _group_find_request_package(self, package, opts):
    """
    Look up the package by its name and return the SR#
    :param package: name of the package
    :param opts: obs options
    """

    url = makeurl(opts.apiurl, ['request'], 'states=new,review&project=openSUSE:Factory&view=collection&package={0}'.format(package))
    f = http_GET(url)
    root = ET.parse(f).getroot()

    res = []
    for rq in root.findall('request'):
        res.append(int(rq.attrib['id']))
        
    if len(res) > 1:
        raise oscerr.ServiceRuntimeError('There are multiple requests for package "{0}"'.format(package))

    if len(res) == 0 or res[0] == 0:
        #raise oscerr.ServiceRuntimeError('There is no request for package "{0}"'.format(package))
        return None

    return res[0]

def _group_find_request_project(self, source_project, opts):
    """
    Look up the source project by its name and return the SR#(s)
    :param source_project: name of the source project
    :param opts: obs options
    """

    url = makeurl(opts.apiurl, ['request'], 'states=new,review&project=openSUSE:Factory&view=collection')
    f = http_GET(url)
    root = ET.parse(f).getroot()

    res = []
    for rq in root.findall('request'):
        for a in rq.findall('action'):
            s = a.find('source')
            if s is not None and s.get('project') == source_project:
                res.append(int(rq.attrib['id']))

    if len(res) == 0:
        #raise oscerr.ServiceRuntimeError('There are no requests for base project "{0}"'.format(source_project))
        return None

    return res

def _group_find_request_group(self, request, opts):
    """
    Look up if the SR# is already in some group and if it is return the ID of GR#
    :param request: name of the source project
    :param opts: obs options
    """

    url = makeurl(opts.apiurl, ['search', 'request', 'id?match=action/grouped/@id={0}'.format(request)] )
    f = http_GET(url)
    root = ET.parse(f).getroot()
    
    res = []
    for rq in root.findall('request'):
        res.append(int(rq.attrib['id']))
        
    if len(res) > 1:
        raise oscerr.ServiceRuntimeError('There are multiple group requests for package "{0}". This should not happen.'.format(request))

    if len(res) == 0 or res[0] == 0:
        #raise oscerr.ServiceRuntimeError('There is no grouping request for package "{0}"'.format(package))
        return None

    return res[0]

def _group_find_sr(self, pkgs, opts):
    """
    Search for all various mutations and return list of SR#s
    :param pkgs: mesh of argumets to search for
    :param opts: obs options
    """

    print("Searching for SR#s based on the arguments...")
    srids = []
    for p in pkgs:
        request = self._group_find_request_package(p, opts)
        if not request:
            request = self._group_find_request_id(p, opts)
        if not request:
            request = self._group_find_request_project(p, opts)
        if not request:
            raise oscerr.ServiceRuntimeError('No SR# found for: {0}'.format(p))
        else:
            srids.append(request)
    
    # Flattens the multi level list we actually have here
    def iterFlatten(root):
        if isinstance(root, (list, tuple)):
            for element in root:
                for e in iterFlatten(element):
                    yield e
        else:
            yield root

    # this is needed in order to ensure we have one level list not nested one
    return list(iterFlatten(srids))

def _group_verify_grouping(self, srids, opts, require_grouping = False):
    """
    Verify that none of the SR#s is not part of any other grouping request
    :param srids: list of submit request IDs
    :param opts: obs options
    :param require_grouping: if passed return list of GR#s for the SR#s and fail if they are not members of any
    """
    
    print("Checking wether the SR#s are already in grouping project...")
    grids = []
    for sr in srids:
        group = self._group_find_request_group(sr, opts)
        if group:
            if require_grouping:
                grids.append(group)
            else:
                raise oscerr.ServiceRuntimeError('SR#{0} is already in GR#{1}'.format(sr, group))
        else:
            if require_grouping:
                # Can't assert as in the automagic group finding we need to pass here
                #raise oscerr.ServiceRuntimeError('SR#{0} is not member of any group request'.format(sr))
                grids.append(0)
    
    return grids

def _group_verify_type(self, grid, opts):
    """
    Verify the GR# to ensure it is grouping request to start with
    :param grid: ID of grouping request we want to verify
    :param opts: obs options
    """

    print("Checking if GR# is proper type...")
    url = makeurl(opts.apiurl, ['search', 'request', 'id?match=(action/@type=\'group\'%20and%20(state/@name=\'new\'%20or@20state/@name=\'review\'))'] )
    f = http_GET(url)
    root = ET.parse(f).getroot()

    res = []
    for rq in root.findall('request'):
        res.append(int(rq.attrib['id']))
    
    # we have various stuff passed, and it might or might not be int we need for the comparison
    try:
        i = int(grid)
    except ValueError:
        #raise oscerr.ServiceRuntimeError('GR#{0} is not proper open grouping request'.format(grid))
        return None

    if not i in res:
        #raise oscerr.ServiceRuntimeError('GR#{0} is not proper open grouping request'.format(grid))
        return None
    
    return i

def _group_create(self, name, pkgs, opts):
    """
    Create grouping request from selected pkgs/project/ids
    :param name: name of the group
    :param pkgs: list of packages to group
    :param opts: obs options
    """

    srids = self._group_find_sr(pkgs, opts)
    self._group_verify_grouping(srids, opts)
    
    # compose the xml
    xml='<request><action type="group">'
    for r in srids:
        xml += "<grouped id='" + str(r) + "'/>"
    xml += '</action><description>' + str(name) + '</description></request>'

    # sent the request to server
    query = {'cmd': 'create'}
    u = makeurl(opts.apiurl, ['request'], query=query)
    f = http_POST(u, data=xml)
    root = ET.parse(f).getroot().attrib['id']
    
    print('Created GR#{0} with following submit requests: {1}'.format(str(root), ', '.join([str(i) for i in srids])))

def _group_add(self, grid, pkgs, opts):
    """
    Append selected packages to grouping request
    :param grid: grouping request id
    :param pkgs: list of packages to append
    :param opts: obs options
    """
    
    # check if first argument is actual grouping ID and if not try to find
    # the group id in other requests
    returned_group = self._group_verify_type(grid, opts)
    if returned_group:
        srids = self._group_find_sr(pkgs, opts)
        self._group_verify_grouping(srids, opts)
    else:
        # here we add the grid to pkgs and search among all to get at least one
        # usefull group request id
        pkgs += (grid,)
        srids = self._group_find_sr(pkgs, opts)

        pkg_grids = self._group_verify_grouping(srids, opts, True)
        pkg_grids = list(set(pkg_grids))
        # if there is 1 group it means we found only the fallback 0
        if len(pkg_grids) == 1:
            raise oscerr.ServiceRuntimeError('There is no grouping request ID among all submitted pacakges:')
        # if the groups are more than 2 we have multiple grouping IDs which is also not good
        if len(pkg_grids) > 2:
            raise oscerr.ServiceRuntimeError('There are multiple grouping request IDs among added pacakges: {0}'.format(', '.join(pkg_grids)))
        grid = pkg_grids[1]
        
        # now remove the package that provided the GR# from the pkgs addition list
        for sr in srids:
            group = self._group_find_request_group(sr, opts)
            if group:
                srids.remove(sr)
    
    for r in srids:
        query = {'cmd': 'addrequest'}
        query['newid'] = str(r)
        url = makeurl(opts.apiurl, ['request', str(grid)], query=query)
        f = http_POST(url)
        root = ET.parse(f).getroot()
        print('Added SR#{0} to group request GR#{1}'.format(r, grid))
        
def _group_remove(self, grid, pkgs, opts):
    """
    Remove selected packages from grouping request
    :param grid: grouping request id
    :param pkgs: list of packages to remove
    :param opts: obs options
    """
    
    srids = self._group_find_sr(pkgs, opts)
    self._group_verify_type(grid, opts)
    srid_groups = self._group_verify_grouping(srids, opts, True)
    
    # ensure there are no mixed packages from different group request
    for i in srid_groups:
        if not int(i) == int(grid):
            raise oscerr.ServiceRuntimeError('Some of the SR#s do not belong to group request GR#{0}'.format(grid))
    
    # remove the SR#s from the GR#
    for r in srids:
        query = {'cmd': 'removerequest'}
        query['oldid'] = str(r)
        u = makeurl(opts.apiurl, ['request', str(grid)], query=query)
        f = http_POST(u)
        root = ET.parse(f).getroot()
        print('Removed SR#{0} from group request GR#{1}'.format(r, grid))

def _print_group_header(self, grid, opts):
    """
    Print header showing the content of the GR#
    :param grid: grouping request id
    :param opts: obs options
    """
    url = makeurl(opts.apiurl, ['request', str(grid)])
    f = http_GET(url)
    root = ET.parse(f).getroot()
    
    description = str(root.find('description').text)
    date = str(root.find('state').attrib['when'])
    author = str(root.find('state').attrib['who'])
    
    print('GR#{0} | {1} | {2} | {3}'.format(grid, author, date, description))
    

def _group_list_requests(self, grid, opts):
    """
    List open grouping requests or list content of one GR#
    :param grid: grouping request id if None lists all open grouping requests
    :param opts: obs options
    """
    
    res = []
    if not grid:
        # search up the GR#s
        url = makeurl(opts.apiurl, ['search', 'request', 'id?match=(action/@type=\'group\'%20and%20(state/@name=\'new\'%20or@20state/@name=\'review\'))'] )
        f = http_GET(url)
        root = ET.parse(f).getroot()
    
        for rq in root.findall('request'):
            res.append(int(rq.attrib['id']))
        
        print('Listing current open grouping requests...')
        for rq in res:
            self._print_group_header(rq, opts)
    else:
        self._print_group_header(grid, opts)
        # FIXME: find all requests in grouping request and print their content
        #        currently the we have to search for all grouping requests and then filter for proper id, highly suboptimal

@cmdln.option('-v', '--version', action='store_true',
              dest='version',
              help='show version of the plugin')
def do_group(self, subcmd, opts, *args):
    """${cmd_name}: group packages into one group for verification
      
    "list" (or "l") will list SR ids grouped into selected group or without
        argument will list all current group requests (GR#)
    
    "add" (or "a") will add package(s) into selected group request or group all packages in to
        group request if one of the packages is in such and no GR# is specified
    
    "create" (or "c") will create new group request with added package(s)
    
    "remove" (or "r") will remove SR#(s) from selected group request
    
    "approve" will approve the GR# and thus all of its SR#s will be merged to factory
    
    Usage:
            osc group list [GR#]
            osc group add [GR#] [package-name | Source:Repository:/ | SR#]
            osc group create "Name of the group" [package-name | Source:Repository:/ | SR#]
            osc group remove GR# [package-name | Source:Repository:/ | SR#]
            osc group approve GR# FIXME: finish this command in obs first

    ${cmd_option_list}
    """

    if opts.version:
        self._print_version()

    # available commands
    cmds = ['list', 'l', 'add', 'a', 'create', 'c', 'remove', 'r', 'approve']
    if not args or args[0] not in cmds:
        raise oscerr.WrongArgs('Unknown grouping action. Choose one of the {0}.'.format(', '.join(cmds)))

    # verify the argument counts match the commands
    cmd = args[0]
    if cmd in ['list', 'l']:
        min_args, max_args = 0, 1
    elif cmd in ['add', 'a', 'remove', 'r']:
        min_args, max_args = 2, None
    elif cmd in ['create', 'c']:
        min_args, max_args = 2, None
    elif cmd in ['approve']:
        min_args, max_args = 1, 1
    else:
        raise oscerr.WrongArgs('Unknown command: {0}'.format(cmd))
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if not max_args is None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # init the obs access
    opts.apiurl = conf.config['apiurl']
    
    if cmd in ['list', 'l']:
        # check if there is GR#
        if len(args) < 2:
            self._group_list_requests(None, opts)
        else:
            self._group_list_requests(args[1], opts)
    elif cmd in ['add', 'a']:
        self._group_add(args[1], args[2:], opts)
    elif cmd in [ 'remove', 'r']:
        self._group_remove(args[1], args[2:], opts)
    elif cmd in ['create', 'c']:
        self._group_create(args[1], args[2:], opts)
    elif cmd in ['approve']:
        self._group_approve(args[1], opts)

#Local Variables:
#mode: python
#py-indent-offset: 4
#tab-width: 8
#End:
