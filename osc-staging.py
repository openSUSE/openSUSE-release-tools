#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# (C) 2014 mhrusecky@suse.cz, openSUSE.org
# (C) 2014 tchvatal@suse.cz, openSUSE.org
# Distribute under GPLv2 or GPLv3

import logging

import osc
from osc import cmdln
from osc.core import *

from stagingapi import StagingApi


OSC_STAGING_VERSION='0.0.1'

def _print_version(self):
    """ Print version information about this extension. """
    print '%s'%(self.OSC_STAGING_VERSION)
    quit(0)


def _get_parent(apirul, project, repo = "standard"):
    """
    Finds what is the parent project of the staging project
    :param apiurl: url to the OBS api
    :param project: staging project to check
    :param repo: which repository to follow
    :return name of the parent project
    """

    url = make_meta_url("prj", project, apiurl)
    data = http_GET(url).readlines()
    root = ET.fromstring(''.join(data))

    p_path = root.find("repository[@name='%s']/path"%(repo))
    if not p_path:
        logging.error("Project '%s' has no repository named '%s'"%(project, repo))
        return None
    return p_path['project']

# Get last build results (optionally only for specified repo/arch)
# Works even when rebuild is triggered
def _get_build_res(opts, prj, repo=None, arch=None):
    query = {}
    query['lastbuild'] = 1
    if repo is not None:
        query['repository'] = repo
    if arch is not None:
        query['arch'] = arch
    u = makeurl(opts.apiurl, ['build', prj, '_result'], query=query)
    f = http_GET(u)
    return f.readlines()

def _get_changed(opts, project, everything):
    ret = []
    # Check for local changes
    for pkg in meta_get_packagelist(opts.apiurl, project):
        if len(ret) != 0 and not everything:
            break
        f = http_GET(makeurl(opts.apiurl, ['source', project, pkg]))
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
        if len(server_diff(opts.apiurl, t, p, r, project, pkg, None, True)) > 0:
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

    ret = 0
    chng = _get_changed(opts, project, check_everything)
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
        f = _get_build_res(opts, project)
        root = ET.fromstring(''.join(f))

        # Get parent project
        m_url = make_meta_url("prj", project, opts.apiurl)
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
            f = _get_build_res(opts, p_project.get("project"), repo=results.get("repository"), arch=results.get("arch"))
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

    req = None

    # We are dealing with sr
    if re.match('^\d+$', trg):
        # read info from sr
        req = get_request(opts.apiurl, trg)
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
    url = make_meta_url('prj', stg_prj, opts.apiurl)
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
    trg_meta_url = make_meta_url("prj", trg_prj, opts.apiurl)
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
    trg_meta_url = make_meta_url("prj", src_prj, opts.apiurl)
    data = http_GET(trg_meta_url).readlines()
    perm += "".join(filter((lambda x: (re.search("^\s+(<person|<group)", x) is not None)), data))

    # add maintainers of source package
    if src_pkg is not None:
        trg_meta_url = make_meta_url("pkg", (src_prj, src_pkg), opts.apiurl)
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
    url = make_meta_url('prj',stg_prj,opts.apiurl,True,False)
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
    chng = _get_changed(opts, project, True)
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
    delete_project(opts.apiurl, project, force=True, msg=None)
    print("Deleted.")
    return

def _staging_submit_devel(self, project, opts):
    """
    Generate new review requests for devel-projects based on our staging changes.
    :param project: staging project to submit into devel projects
    """
    chng = _get_changed(opts, project, True)
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

                u = makeurl(opts.apiurl, ['request'], query='cmd=create&addrevision=1')
                f = http_POST(u, data=action_xml)

                root = ET.parse(f).getroot()
                print("Created request %s" % (root.get('id')))
    else:
        print("No changes to submit")
    return

def _staging_change_review_state(self, opts, id, newstate, by_group='', by_user='', message='', supersed=None):
    """ taken from osc/osc/core.py, improved:
        - verbose option added,
        - empty by_user=& removed.
        - numeric id can be int().
    """
    query = {'cmd': 'changereviewstate', 'newstate': newstate }
    if by_group:  query['by_group'] = by_group
    if by_user:   query['by_user'] = by_user
    if supersed: query['superseded_by'] = supersed
#    if message: query['comment'] = message
    u = makeurl(opts.apiurl, ['request', str(id)], query=query)
    f = http_POST(u, data=message)
    root = ET.parse(f).getroot()
    return root.attrib['code']

def _staging_get_rings(self, opts):
    ret = dict()
    for prj in ['openSUSE:Factory:Rings:0-Bootstrap', 'openSUSE:Factory:Rings:1-MinimalX']:
        u = makeurl(opts.apiurl, ['source', prj])
        f = http_GET(u)
        for entry in ET.parse(f).getroot().findall('entry'):
            ret[entry.attrib['name']] = prj
    return ret

def _staging_one_request(self, rq, opts):
    if (opts.verbose):
        ET.dump(rq)
        print(opts)
    id = int(rq.get('id'))
    act_id = 0
    approved_actions = 0
    actions = rq.findall('action')
    act = actions[0]

    tprj = act.find('target').get('project')
    tpkg = act.find('target').get('package')

    e = []
    if not tpkg:
        e.append('no target/package in request %d, action %d; ' % (id, act_id))
    if not tprj:
        e.append('no target/project in request %d, action %d; ' % (id, act_id))
    # it is no error, if the target package dies not exist

    ring = self.rings.get(tpkg, None)
    if ring is None:
        msg = "ok"
    else:
        stage_info = self.packages_staged.get(tpkg, ('', 0))
        if stage_info[0] == self.letter_to_accept and int(stage_info[1]) == id:
            # TODO make api for that
            stprj = 'openSUSE:Factory:Staging:%s' % self.letter_to_accept
            msg = 'ok, tested in %s' % stprj
            delete_package(opts.apiurl, stprj, tpkg, msg='done')
        elif stage_info[1] != 0 and int(stage_info[1]) != id:
            print stage_info
            print "osc staging select %s %s" % (stage_info[0], id)
            return
        elif stage_info[1] != 0: # keep silent about those already asigned
            return
        else:
            print "Request(%d): %s -> %s" % (id, tpkg, ring)
            return

    self._staging_change_review_state(opts, id, 'accepted', by_group='factory-staging', message=msg)

def _staging_check_one_source(self, flink, si, opts):
    package = si.get('package')
    # we have to check if its a link within the staging project
    # in this case we need to keep the link as is, and not freezing
    # the target. Otherwise putting kernel-source into staging prj
    # won't get updated kernel-default (and many other cases)
    for linked in si.findall('linked'):
        if linked.get('project') in self.projectlinks:
            # take the unexpanded md5 from Factory link
            url = makeurl(opts.apiurl, ['source', 'openSUSE:Factory', package], { 'view': 'info', 'nofilename': '1' })
            #print package, linked.get('package'), linked.get('project')
            f = http_GET(url)
            proot = ET.parse(f).getroot()
            ET.SubElement(flink, 'package', { 'name': package, 'srcmd5': proot.get('lsrcmd5'), 'vrev': si.get('vrev') })
            return package
    ET.SubElement(flink, 'package', { 'name': package, 'srcmd5': si.get('srcmd5'), 'vrev': si.get('vrev') })
    return package

def _staging_receive_sources(self, prj, sources, flink, opts):
    url = makeurl(opts.apiurl, ['source', prj], { 'view': 'info', 'nofilename': '1' } )
    f = http_GET(url)
    root = ET.parse(f).getroot()

    for si in root.findall('sourceinfo'):
        package = self._staging_check_one_source(flink, si, opts)
        sources[package] = 1
    return sources

def _staging_freeze_prjlink(self, prj, opts):
    url = makeurl(opts.apiurl, ['source', prj, '_meta'])
    f = http_GET(url)
    root = ET.parse(f).getroot()
    sources = dict()
    flink = ET.Element('frozenlinks')
    links = root.findall('link')
    links.reverse()
    self.projectlinks = []
    for link in links:
        self.projectlinks.append(link.get('project'))

    for lprj in self.projectlinks:
        fl = ET.SubElement(flink, 'frozenlink', { 'project': lprj } )
        sources = self._staging_receive_sources(lprj, sources, fl, opts)

    url = makeurl(opts.apiurl, ['source', prj, '_project', '_frozenlinks'], { 'meta': '1' } )
    f = http_PUT(url, data=ET.tostring(flink))
    root = ET.parse(f).getroot()
    print ET.tostring(root)

def _staging_cleanup_rings(self, opts):
    self.bin2src = dict()
    self.pkgdeps = dict()
    self.sources = list()
    self._staging_check_depinfo_ring('openSUSE:Factory:Rings:0-Bootstrap', 'openSUSE:Factory:Rings:1-MinimalX', opts);
    self._staging_check_depinfo_ring('openSUSE:Factory:Rings:1-MinimalX', 'openSUSE:Factory:MainDesktops', opts);

def _staging_fill_pkgdeps(self, prj, repo, arch, opts):
    url = makeurl(opts.apiurl, ['build', prj, repo, arch, '_builddepinfo'])
    f = http_GET(url)
    root = ET.parse(f).getroot()

    for package in root.findall('package'):
        #print ET.tostring(package)
        source = package.find('source').text
        if package.attrib['name'].startswith('preinstall'):
            continue
        self.sources.append(source)

        for subpkg in package.findall('subpkg'):
            subpkg = subpkg.text
            if self.bin2src.has_key(subpkg):
                print "bin $s defined twice $prj $source - $bin2src{$s}\n"
            self.bin2src[subpkg] = source

    for package in root.findall('package'):
        source = package.find('source').text
        for pkg in package.findall('pkgdep'):
            if not self.bin2src.has_key(pkg.text):
                if pkg.text.startswith('texlive-'):
                    for letter in range(ord('a'), ord('z') + 1):
                        self.pkgdeps['texlive-specs-' + chr(letter)] = 'texlive-specs-' + chr(letter)
                else:
                    print "PKG NOT THERE", pkg.text
                continue
            b = self.bin2src[pkg.text]
            self.pkgdeps[b] = source

def _staging_check_depinfo_ring(self, prj, nextprj, opts):
  self._staging_fill_pkgdeps(prj, 'standard', 'x86_64', opts)

  if prj == 'openSUSE:Factory:Rings:1-MinimalX':
      url = makeurl(opts.apiurl, ['build', prj, 'images', 'x86_64', 'Test-DVD-x86_64', '_buildinfo'] )
      root = ET.parse(http_GET(url)).getroot()
      for bdep in root.findall('bdep'):
          if not bdep.attrib.has_key('name'): continue
          b = bdep.attrib['name']
          if not self.bin2src.has_key(b): continue
          b = self.bin2src[b]
          self.pkgdeps[b] = 'MYdvd'

  # if ($prj eq 'openSUSE:Factory:MainDesktops') {
  #   $dinfo->{MYcds} = {};
  #   $dinfo->{MYcds}->{pkgdep} = ();
  #   $dinfo->{MYcds}->{source} = 'MYcds';
  #   push(@{$dinfo->{MYcds}->{pkgdep}}, 'kiwi-image-livecd-gnome');
  #   push(@{$dinfo->{MYcds}->{pkgdep}}, 'kiwi-image-livecd-kde');

  if prj == 'openSUSE:Factory:Rings:0-Bootstrap':
      url = makeurl(opts.apiurl, ['build', prj, 'standard', '_buildconfig'] )
      for line in http_GET(url).read().split('\n'):
          if line.startswith('Preinstall:') or line.startswith('Support:'):
              for prein in line.split(':')[1].split():
                  if not self.bin2src.has_key(prein): continue
                  b = self.bin2src[prein]
                  self.pkgdeps[b] = 'MYinstall'

  for source in self.sources:
      #   next if ($key =~ m/^MY/ || $key =~ m/^texlive-specs-/ || $key =~ m/^kernel-/);
      if not self.pkgdeps.has_key(source):
          print "osc rdelete -m cleanup", prj, source
          if nextprj:
              print "osc linkpac -c openSUSE:Factory", source, nextprj


@cmdln.option('-e', '--everything', action='store_true',
              help='during check do not stop on first first issue and show them all')
@cmdln.option('-p', '--parent', metavar='TARGETPROJECT',
              help='manually specify different parent project during creation of staging')
@cmdln.option('-m', '--message', metavar='TEXT',
              help='manually specify different parent project during creation of staging')
@cmdln.option('-v', '--version', action='store_true',
              help='show version of the plugin')
def do_staging(self, subcmd, opts, *args):
    """${cmd_name}: Commands to work with staging projects

    "check" will check if all packages are links without changes

    "create" (or "c") will create staging repo from specified submit request

    "remove" (or "r") will delete the staging project into submit requests for openSUSE:Factory

    "submit-devel" (or "s") will create review requests for changed packages in staging project
        into their respective devel projects to obtain approval from maitnainers for pushing the
        changes to openSUSE:Factory

    "freeze" will freeze the sources of the project's links (not affecting the packages actually in)

    "accept" will accept all requests openSUSE:Factory:Staging:<LETTER>

    "list" will pick the requests not in rings

    "select" will add requests to the project

    Usage:
        osc staging check [--everything] REPO
        osc staging create [--parent project] SR#
        osc staging create [--parent project] PROJECT[/PACKAGE]
        osc staging remove REPO
        osc staging submit-devel [-m message] REPO
        osc staging freeze PROJECT
        osc staging list
        osc staging select LETTER REQUEST...
        osc staging accept LETTER
        osc staging cleanup_rings
    """
    if opts.version:
        self._print_version()

    # verify the argument counts match the commands
    if len(args) == 0:
        raise oscerr.WrongArgs('No command given, see "osc help staging"!')
    cmd = args[0]
    if cmd in ['submit-devel', 's', 'remove', 'r', 'accept', 'freeze']:
        min_args, max_args = 1, 1
    elif cmd in ['check']:
        min_args, max_args = 1, 2
    elif cmd in ['select']:
        min_args, max_args = 2, None
    elif cmd in ['create', 'c']:
        min_args, max_args = 1, 2
    elif cmd in ['list', 'cleanup_rings']:
        min_args, max_args = 0, 0
    else:
        raise oscerr.WrongArgs('Unknown command: %s'%(cmd))
    if len(args) - 1 < min_args:
        raise oscerr.WrongArgs('Too few arguments.')
    if not max_args is None and len(args) - 1 > max_args:
        raise oscerr.WrongArgs('Too many arguments.')

    # init the obs access
    opts.apiurl = self.get_api_url()
    opts.verbose = False

    self.rings = self._staging_get_rings(opts)
    api = StagingApi(opts.apiurl)

    # call the respective command and parse args by need
    if cmd in ['push', 'p']:
        project = args[1]
        self._staging_push(project, opts)
    elif cmd in ['create', 'c']:
        sr = args[1]
        self._staging_create(sr, opts)
    elif cmd in ['check']:
        project = args[1]
        return self._staging_check(project, opts.everything, opts)
    elif cmd in ['remove', 'r']:
        project = args[1]
        self._staging_remove(project, opts)
    elif cmd in ['submit-devel', 's']:
        project = args[1]
        self._staging_submit_devel(project, opts)
    elif cmd in ['freeze']:
        self._staging_freeze_prjlink(args[1], opts)
    elif cmd in ['select']:
        # TODO: have an api call for that
        stprj = 'openSUSE:Factory:Staging:%s' % args[1]
        for i in range(2, len(args)):
            api.sr_to_prj(args[i], stprj)
    elif cmd in ['cleanup_rings']:
        self._staging_cleanup_rings(opts)
    elif cmd in ['accept', 'list']:
        self.letter_to_accept = None
        if cmd == 'accept':
            self.letter_to_accept = args[1]

        self.packages_staged = dict()
        for prj in api.get_staging_projects():
            meta = api.get_prj_pseudometa(prj)
            for req in meta['requests']:
                self.packages_staged[req['package']] = (prj[-1], req['id'])

        # xpath query, using the -m, -r, -s options
        where = "@by_group='factory-staging'+and+@state='new'"

        url = makeurl(opts.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            tprj = rq.find('action/target').get('project')
            self._staging_one_request(rq, opts)

        if self.letter_to_accept:
            url = makeurl(opts.apiurl, ['source', 'openSUSE:Factory:Staging:%s' % self.letter_to_accept])
            f = http_GET(url)
            root = ET.parse(f).getroot()
            print ET.tostring(root)

