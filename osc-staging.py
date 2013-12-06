#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# (C) 2013 mhrusecky@suse.cz, openSUSE.org
# (C) 2013 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

OSC_STAGING_VERSION='0.0.1'

def _print_version(self):
    """ Print version information about this extension. """
    print '%s'%(self.OSC_STAGING_VERSION)
    quit(0)

def _staging_check(self, project, check_everything, opts):
    """
    Checks whether project does not contain local changes
    and whether it contains only links
    :param project: staging project to check
    :param everything: do not stop on first verification failure
    :param opts: pointer to options
    """
    apiurl = self.get_api_url()

    ret = 0
    for pkg in meta_get_packagelist(apiurl, project):
        if ret == 1 and not check_everything:
            break
        f = http_GET(makeurl(apiurl, ['source', project, pkg]))
        linkinfo = ET.parse(f).getroot().find('linkinfo')
        if linkinfo is None:
            print >>sys.stderr, 'Error: Not a source link: %s/%s'%(project,pkg)
            ret = 1
            continue
        if linkinfo.get('error'):
            print >>sys.stderr, 'Error: Broken source link: %s/%s'%(project, pkg)
            ret = 1
            continue
        t = linkinfo.get('project')
        p = linkinfo.get('package')
        r = linkinfo.get('revision')
        if len(server_diff(apiurl, t, p, r, project, pkg, None, True)) > 0:
            print >>sys.stderr, 'Error: Has local modifications: %s/%s'%(project, pkg)
            ret = 1
            continue
    return ret

def _staging_create(self, sr, opts):
    """
    Creates new staging project based on the submit request.
    :param sr: submit request containing package to test directed for openSUSE:Factory
    :param opts: pointer to options
    """

    apiurl = self.get_api_url()

    # read info from sr
    req = get_request(apiurl, sr)
    act = req.get_actions("submit")[0]

    trg_prj = act.tgt_project
    trg_pkg = act.tgt_package
    src_prj = act.src_project
    src_pkg = act.src_package
    stg_prj = trg_prj + ":Staging:" + trg_pkg

    # test if staging project exists
    found = 1
    url = make_meta_url('prj', stg_prj, apiurl)
    try:
       data = http_GET(url).readlines()
    except HTTPError as e:
       if e.code == 404:
            found = 0
       else:
            raise e
    if found == 1:
        print('Such a staging project already exists, overwrite? (Y/n)')
        answer = sys.stdin.readline()
        if re.search("^\s*[Nn]", answer):
            print('Aborting...')
            exit(1)
 
    # parse metadata from parent project
    trg_meta_url = make_meta_url("prj", trg_prj, apiurl)
    data = http_GET(trg_meta_url).readlines()

    dis_repo = []
    en_repo = []
    repos = []
    perm =''
    in_build = 0
    for line in data:
        # what repositories are disabled
        if in_build == 1:
            if re.search("^\s+</build>", line):
                in_build = 0
            elif re.search("^\s+<disable", line):
                dis_repo.append(re.sub(r'.*repository="([^"]+)".*', r'\1', line).strip())
            elif re.search("^\s+<enable", line):
                en_repo.append(re.sub(r'.*repository="([^"]+)".*', r'\1', line).strip())
        elif re.search("^\s+<build>", line):
            in_build=1
        # what are the rights
        elif re.search("^\s+(<person|<group)", line):
                perm += line
        # what are the repositories
        elif re.search("^\s+<repository", line):
                repos.append(re.sub(r'.*name="([^"]+)".*', r'\1', line).strip())
   
    # add maintainers of source project
    trg_meta_url = make_meta_url("prj", src_prj, apiurl)
    data = http_GET(trg_meta_url).readlines()
    perm += "".join(filter((lambda x: (re.search("^\s+(<person|<group)", x) != None)), data))
    
    # add maintainers of source package
    trg_meta_url = make_meta_url("pkg", (src_prj, src_pkg), apiurl)
    data = http_GET(trg_meta_url).readlines()
    perm += "".join(filter((lambda x: (re.search("^\s+(<person|<group)", x) != None)), data))

    # create xml for new project
    new_xml  = '<project name="%s">\n'%(stg_prj)
    new_xml += '  <title>Staging project for package %s (sr#%s)</title>\n'%(trg_pkg, req.reqid)
    new_xml += '  <description></description>\n'
    new_xml += '  <link project="%s"/>\n'%(trg_prj)
    new_xml += '  <person userid="%s" role="maintainer"/>\n'%(req.get_creator())
    new_xml += perm
    new_xml += '  <build><enable/></build>\n'
    new_xml += '  <debuginfo><enable/></debuginfo>\n'
    new_xml += '  <publish><disable/></publish>\n'
    for repo in repos:
        if repo not in dis_repo:
            new_xml += '  <repository name="%s" rebuild="direct" linkedbuild="all">\n'%(repo)
            new_xml += '    <path project="%s" repository="%s"/>\n'%(trg_prj,repo)
            new_xml += '    <arch>i586</arch>\n'
            new_xml += '    <arch>x86_64</arch>\n'
            new_xml += '  </repository>\n'
    new_xml += '</project>\n'

    # creation of new staging project
    print('Creating staging project "%s"...'%(stg_prj))
    url = make_meta_url('prj',stg_prj,apiurl,True,False)
    f = metafile(url, new_xml, False)
    http_PUT(f.url, file=f.filename)

    # link package there
    print('Linking package %s/%s -> %s/%s...'%(src_pkg,src_prj,stg_prj,trg_pkg))
    link_pac(src_prj, src_pkg, stg_prj, trg_pkg, True)
    print
    
    return

def _staging_remove(self, project, opts):
    """
    Remove staging project.
    :param project: staging project to delete
    :param opts: pointer to options
    """
    apiurl = self.get_api_url()
    delete_project(apiurl, project, force=True, msg=None)
    print("Deleted.")
    return

def _staging_push(self, project, opts):
    """
    Generate new submit requests group based on staging project.
    :param project: staging project to submit
    :param opts: pointer to options
    """
    apiurl = self.get_api_url()
    if not self._staging_check(apiurl, project):
        raise oscerr.ServiceRuntimeError('Verification of staging repo failed.')

    # loop over packages
    for pkg in meta_get_packagelist(apiurl, project):
        # decompose symlinks
        u = makeurl(apiurl, ['source', project, pkg])
        f = http_GET(u)
        root = ET.parse(f).getroot()
        linkinfo = root.find('linkinfo')
        if linkinfo == None:
            print >>sys.stderr, "Not a source link: %s"%(pkg)
            quit(1)
        if linkinfo.get('error'):
            print >>sys.stderr, "Broken source link: %s"%(pkg)
            quit(1)
        t = linkinfo.get('project')
        p = linkinfo.get('package')
        r = linkinfo.get('revision')
        # Get rid of old requests
        for rq in get_exact_request_list(apiurl, t, project, pkg, pkg, ('new', 'review')):
            # obsolete submit requests that contain the package (notify!)
            print('.')
    # sent new submitrequest for the package

def _staging_submit_devel(self, project, opts):
    """
    Generate new review requests for devel-projects based on our staging changes.
    :param apiurl: pointer to obs api url link
    :param project: staging project to submit into devel projects
    """
    print("Not implemented.")
    return


@cmdln.option('-e', '--everything', action='store_true', dest='everything',
              help='during check do not stop on first first issue and show them all')
@cmdln.option('-v', '--version', action='store_true',
              dest='version',
              help='show version of the plugin')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    "check" will check if all packages are links without changes

    "create" (or "c") will create staging repo from specified submit request

    "push" (or "p") will push the staging project into grouped submit requests for openSUSE:Factory

    "remove" (or "r") will delete the staging project into submit requests for openSUSE:Factory

    "submit-devel" (or "s") will create review requests for changed packages in staging project
        into their respective devel projects to obtain approval from maitnainers for pushing the
        changes to openSUSE:Factory

    Usage:
        osc staging check [--everything] REPO
        osc staging create SR#
        osc staging push REPO
        osc staging remove REPO
        osc stating submit-devel REPO
    """
    if opts.version:
        self._print_version()

    # available commands
    cmds = ['check', 'push', 'p', 'create', 'c', 'remove', 'r']
    if not args or args[0] not in cmds:
        raise oscerr.WrongArgs('Unknown stagings action. Choose one of the %s.'%(', '.join(cmds)))

    # verify the argument counts match the commands
    cmd = args[0]
    if cmd in ['push', 'p', 'submit-devel', 's', 'remove', 'r']:
        min_args, max_args = 1, 1
    elif cmd in ['check']:
        min_args, max_args = 1, 2
    elif cmd in ['create', 'c']:
        min_args, max_args = 1, 1
    else:
        raise RuntimeError('Unknown command: %s'%(cmd))
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if not max_args is None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # init the obs access
    apiurl = self.get_api_url()
    
    # check for the opts
    staging_check_everything = False
    if opts.everything:
        staging_check_everything = True

    # call the respective command and parse args by need
    if cmd in ['push', 'p']:
        project = args[1]
        self._staging_push(project, opts)
    elif cmd in ['create', 'c']:
        sr = args[1]
        self._staging_create(sr, opts)
    elif cmd in ['check']:
        project = args[1]
        self._staging_check(project, staging_check_everything, opts)
    elif cmd in ['remove', 'r']:
        project = args[1]
        self._staging_remove(project, opts)
    elif cmd in ['submit-devel', 's']:
        project = args[1]
        self._staging_submit_devel(project, opts)
