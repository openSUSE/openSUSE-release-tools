#!/usr/bin/env python
# -*- coding: UTF-8 -*-
#
# (C) 2013 mhrusecky@suse.cz, openSUSE.org
# (C) 2013 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

from osc import cmdln
from osc import conf
from osc import commandline

OSC_STAGING_VERSION='0.0.1'

def _print_version(self):
    """ Print version information about this extension. """
    print '%s'%(self.OSC_STAGING_VERSION)
    quit(0)

# Get last build results (optionally only for specified repo/arch)
# Works even when rebuild is triggered
def _get_build_res(apiurl, prj, repo=None, arch=None):
    query = {}
    query['lastbuild'] = 1
    if repo is not None:
        query['repository'] = repo
    if arch is not None:
        query['arch'] = arch
    u = makeurl(apiurl, ['build', prj, '_result'], query=query)
    f = http_GET(u)
    return f.readlines()

def _get_changed(apiurl, project, everything):
    ret = []
    # Check for local changes
    for pkg in meta_get_packagelist(apiurl, project):
        if len(ret) != 0 and not everything:
            break
        f = http_GET(makeurl(apiurl, ['source', project, pkg]))
        linkinfo = ET.parse(f).getroot().find('linkinfo')
        if linkinfo is None:
            ret.append({'pkg': pkg, 'code': 'NOT_LINK', 'msg': 'Not a source link'})
            continue
        if linkinfo.get('error'):
            ret.append({'pkg': pkg, 'code': 'BROKEN', 'msg': 'Broken source link'})
            continue
        t = linkinfo.get('project')
        p = linkinfo.get('package')
        r = linkinfo.get('revision')
        if len(server_diff(apiurl, t, p, r, project, pkg, None, True)) > 0:
            ret.append({'pkg': pkg, 'code': 'MODIFIED', 'msg': 'Has local modifications', 'pprj': t, 'ppkg': p})
            continue
    return ret


# Checks the state of staging repo (local modifications, regressions, ...)
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
    chng = _get_changed(apiurl, project, check_everything)
    if len(chng) > 0:
        for pair in chng:
            print >>sys.stderr, 'Error: Package "%s": %s'%(pair['pkg'],pair['msg'])
        print >>sys.stderr, "Error: Check for local changes failed"
        ret = 1
    else:
        print "Check for local changes passed"

    # Check for regressions
    root = None
    if ret == 0 or check_everything:
        print "Getting build status, this may take a while"
        # Get staging project results
        f = _get_build_res(apiurl, project)
        root = ET.fromstring(''.join(f))

        # Get parent project
        m_url = make_meta_url("prj", project, apiurl)
        m_data = http_GET(m_url).readlines()
        m_root = ET.fromstring(''.join(m_data))

        print "Comparing build statuses, this may take a while"

    # Iterate through all repos/archs
    if root is not None and root.find('result') is not None:
        for results in root.findall('result'):
            if ret != 0 and not check_everything:
                break
            if results.get("state") not in [ "published", "unpublished" ]:
                print >>sys.stderr, "Warning: Building not finished yet for %s/%s (%s)!"%(results.get("repository"),results.get("arch"),results.get("state"))
                ret |= 2

            # Get parent project results for this repo/arch
            p_project = m_root.find("repository[@name='%s']/path"%(results.get("repository")))
            if p_project == None:
                print >>sys.stderr, "Error: Can't get path for '%s'!"%results.get("repository")
                ret |= 4
                continue
            f = _get_build_res(apiurl, p_project.get("project"), repo=results.get("repository"), arch=results.get("arch"))
            p_root = ET.fromstring(''.join(f))

            # Find corresponding set of results in parent project
            p_results = p_root.find("result[@repository='%s'][@arch='%s']"%(results.get("repository"),results.get("arch")))
            if p_results == None:
                print >>sys.stderr, "Error: Inconsistent setup!"
                ret |= 4
            else:
                # Iterate through packages
                for node in results:
                    if ret != 0 and not check_everything:
                        break
                    result = node.get("code")
                    # Skip not rebuilt
                    if result in [ "blocked", "building", "disabled" "excluded", "finished", "unknown", "unpublished", "published" ]:
                        continue
                    # Get status of package in parent project
                    p_node = p_results.find("status[@package='%s']"%(node.get("package")))
                    if p_node == None:
                        p_result = None
                    else:
                        p_result = p_node.get("code")
                    # Skip packages not built in parent project
                    if p_result in [ None, "disabled", "excluded", "unknown", "unresolvable" ]:
                        continue
                    # Find regressions
                    if result in [ "broken", "failed", "unresolvable" ] and p_result not in [ "blocked", "broken", "failed" ]:
                        print >>sys.stderr, "Error: Regression (%s -> %s) in package '%s' in %s/%s!"%(p_result, result, node.get("package"),results.get("repository"),results.get("arch"))
                        ret |= 8
                    # Find fixed builds
                    if result in [ "succeeded" ] and result != p_result:
                        print "Package '%s' fixed (%s -> %s) in staging for %s/%s."%(node.get("package"), p_result, result, results.get("repository"),results.get("arch"))

    if ret != 0:
        print "Staging check failed!"
    else:
        print "Staging check succeeded!"
    return ret

def _staging_create(self, trg, opts):
    """
    Creates new staging project based on the submit request.
    :param trg: submit request to create staging project for or parent project/package
    :param opts: pointer to options
    """

    apiurl = self.get_api_url()
    req = None

    # We are dealing with sr
    if re.match('^\d+$', trg):
        # read info from sr
        req = get_request(apiurl, trg)
        act = req.get_actions("submit")[0]

        trg_prj = act.tgt_project
        trg_pkg = act.tgt_package
        src_prj = act.src_project
        src_pkg = act.src_package

    # We are dealing with project
    else:
        data = re.split('/', trg)
        o_stg_prj = data[0]
        trg_prj = re.sub(':Staging:.*','',data[0])
        src_prj = re.sub(':Staging:.*','',data[0])
        if len(data)>1:
            trg_pkg = data[1]
            src_pkg = data[1]
        else:
            trg_pkg = None
            src_pkg = None

    # Set staging name and maybe parent
    if trg_pkg is not None:
        stg_prj = trg_prj + ":Staging:" + trg_pkg

    if re.search(':Staging:',trg):
        stg_prj = o_stg_prj

    if opts.parent:
        trg_prj = opts.parent

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
        print('Staging project "%s" already exists, overwrite? (Y/n)'%(stg_prj))
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
    perm += "".join(filter((lambda x: (re.search("^\s+(<person|<group)", x) is not None)), data))
    
    # add maintainers of source package
    if src_pkg is not None:
        trg_meta_url = make_meta_url("pkg", (src_prj, src_pkg), apiurl)
        data = http_GET(trg_meta_url).readlines()
        perm += "".join(filter((lambda x: (re.search("^\s+(<person|<group)", x) is not None)), data))

    # create xml for new project
    new_xml  = '<project name="%s">\n'%(stg_prj)
    if req is not None:
        new_xml += '  <title>Staging project for package %s (sr#%s)</title>\n'%(trg_pkg, req.reqid)
    else:
        new_xml += '  <title>Staging project "%s"</title>\n'%(trg)
    new_xml += '  <description></description>\n'
    new_xml += '  <link project="%s"/>\n'%(trg_prj)
    if req is not None:
        new_xml += '  <person userid="%s" role="maintainer"/>\n'%(req.get_creator())
    new_xml += perm
    new_xml += '  <build><enable/></build>\n'
    new_xml += '  <debuginfo><enable/></debuginfo>\n'
    new_xml += '  <publish><disable/></publish>\n'
    for repo in repos:
        if repo not in dis_repo:
            new_xml += '  <repository name="%s" rebuild="direct" linkedbuild="localdep">\n'%(repo)
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
    if src_pkg is not None and trg_pkg is not None:
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
    chng = _get_changed(apiurl, project, True)
    if len(chng) > 0:
        print('Staging project "%s" is not clean:'%(project))
        print('')
        for pair in chng:
            print(' * %s : %s'%(pair['pkg'],pair['msg']))
        print('')
        print('Really delete? (N/y)')
        answer = sys.stdin.readline()
        if not re.search("^\s*[Yy]", answer):
            print('Aborting...')
            exit(1)
    delete_project(apiurl, project, force=True, msg=None)
    print("Deleted.")
    return

def _staging_submit_devel(self, project, opts):
    """
    Generate new review requests for devel-projects based on our staging changes.
    :param apiurl: pointer to obs api url link
    :param project: staging project to submit into devel projects
    """
    apiurl = self.get_api_url()
    chng = _get_changed(apiurl, project, True)
    msg = "Fixes from staging project %s" % project
    if opts.message is not None:
        msg = opts.message
    if len(chng) > 0:
        for pair in chng:
            if pair['code'] != 'MODIFIED':
                print >>sys.stderr, 'Error: Package "%s": %s'%(pair['pkg'],pair['msg'])
            else:
                print('Sending changes back %s/%s -> %s/%s'%(project,pair['pkg'],pair['pprj'],pair['ppkg']))
                action_xml  = '<request>';
                action_xml += '   <action type="submit"> <source project="%s" package="%s" /> <target project="%s" package="%s" />' % (project, pair['pkg'], pair['pprj'], pair['ppkg'])
                action_xml += '   </action>'
                action_xml += '   <state name="new"/> <description>%s</description>' % msg
                action_xml += '</request>'

                u = makeurl(apiurl, ['request'], query='cmd=create&addrevision=1')
                f = http_POST(u, data=action_xml)

                root = ET.parse(f).getroot()
                print("Created request %s" % (root.get('id')))
    else:
        print("No changes to submit")
    return


@cmdln.option('-e', '--everything', action='store_true', dest='everything',
              help='during check do not stop on first first issue and show them all')
@cmdln.option('-p', '--parent', metavar='TARGETPROJECT',
              help='manually specify different parent project during creation of staging')
@cmdln.option('-m', '--message', metavar='TEXT',
              help='manually specify different parent project during creation of staging')
@cmdln.option('-v', '--version', action='store_true',
              dest='version',
              help='show version of the plugin')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    "check" will check if all packages are links without changes

    "create" (or "c") will create staging repo from specified submit request

    "remove" (or "r") will delete the staging project into submit requests for openSUSE:Factory

    "submit-devel" (or "s") will create review requests for changed packages in staging project
        into their respective devel projects to obtain approval from maitnainers for pushing the
        changes to openSUSE:Factory

    Usage:
        osc staging check [--everything] REPO
        osc staging create [--parent project] SR#
        osc staging create [--parent project] PROJECT[/PACKAGE]
        osc staging remove REPO
        osc stating submit-devel [-m message] REPO
    """
    if opts.version:
        self._print_version()

    # available commands
    cmds = ['check', 'create', 'c', 'remove', 'r', 'submit-devel', 's']
    if not args or args[0] not in cmds:
        raise oscerr.WrongArgs('Unknown stagings action. Choose one of the %s.'%(', '.join(cmds)))

    # verify the argument counts match the commands
    cmd = args[0]
    if cmd in ['submit-devel', 's', 'remove', 'r']:
        min_args, max_args = 1, 1
    elif cmd in ['check']:
        min_args, max_args = 1, 2
    elif cmd in ['create', 'c']:
        min_args, max_args = 1, 2
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
        return self._staging_check(project, staging_check_everything, opts)
    elif cmd in ['remove', 'r']:
        project = args[1]
        self._staging_remove(project, opts)
    elif cmd in ['submit-devel', 's']:
        project = args[1]
        self._staging_submit_devel(project, opts)
