#
# (C) 2011 coolo@suse.de, Novell Inc, openSUSE.org
# Distribute under GPLv2 or GPLv3
#
# Copy this script to ~/.osc-plugins/ or /var/lib/osc-plugins .
# Then try to run 'osc checker --help' to see the usage.

import socket
import os
import traceback
import subprocess

try:
    fqdn = socket.gethostbyaddr(socket.gethostname())[0]
except:
    fqdn = os.uname()[1]

def _checker_change_review_state(self, opts, id, newstate, by_group='', by_user='', message='', supersed=None):
    """ taken from osc/osc/core.py, improved:
        - verbose option added,
        - empty by_user=& removed.
        - numeric id can be int().
    """
    query = {'cmd': 'changereviewstate', 'newstate': newstate }
    if by_group:  query['by_group'] = by_group
    if by_user:   query['by_user'] = by_user
    if supersed: query['superseded_by'] = supersed
    if message: query['comment'] = message
    u = makeurl(opts.apiurl, ['request', str(id)], query=query)
    f = http_POST(u)
    root = ET.parse(f).getroot()
    return root.attrib['code']

def _checker_checkout_add(self, prj, pkg, rev, opts):
    dir = opts.directory + '/' + re.sub('.*//', '', opts.apiurl) + '/' + prj + '/' + pkg
    if rev: dir = dir + '%r' + rev
    if opts.no_op:
        print "package NOT checked out to " + dir
    else:
        oldcwd = os.getcwd()
        do_co = True

        try:
            os.rmdir(dir)     # remove if empty.
        except:
            pass

        if os.path.exists(dir):
            print "Oops, %s already checked out.\n Please remove to pull a fresh copy." % dir
            return

        o_umask = os.umask(002)             # allow group writable
        try:
            os.makedirs(dir, mode=0777)       # ask for (at least) group writable
        except Exception,e:
            do_co = False
            print "os.makedirs(%s) failed: %s" % (dir, str(e))
        os.umask(o_umask)

        if do_co:
            os.chdir(dir)
            nc = conf.config['checker_checkout_no_colon']
            conf.config['checker_checkout_no_colon'] = False
            conf.config['checker_checkout_rooted'] = False
            conf.config['package_tracking'] = False

            # FIXME: this creates .../PACKAGE-rREV_NR/PACKAGE/.. - can we skip the extra /PACKAGE/ there?
            checkout_package(opts.apiurl, prj, pkg, revision=rev, pathname=dir, server_service_files=True, expand_link=True)
            if opts.origin:
                f = open('.origin', 'wb')
                f.write(opts.origin)
                f.close()

            conf.config['checker_checkout_no_colon'] = nc
            os.chdir(oldcwd)


def _check_repo(self, repo):
    allfine = True
    for arch in repo.findall('arch'):
        if not (arch.attrib['result'] in ['succeeded', 'excluded']) or arch.attrib.has_key('missing'):
            allfine = False
    return allfine

def _checker_one_request(self, rq, cmd, opts):
    if (opts.verbose):
        ET.dump(rq)
        print(opts)
    id = int(rq.get('id'))
    act_id = 0
    approved_actions = 0
    actions = rq.findall('action')
    for act in actions:
        act_id += 1
        _type = act.get('type');
        if (_type == "submit"):
            pkg = act.find('source').get('package')
            prj = act.find('source').get('project')
            rev = act.find('source').get('rev')
            tprj = act.find('target').get('project')
            tpkg = act.find('target').get('package')

            src = HASH({ 'package': pkg, 'project': prj, 'rev':rev, 'error': None })
            e = []
            if not pkg:
                e.append('no source/package in request %d, action %d' % (id, act_id))
            if not prj:
                e.append('no source/project in request %d, action %d' % (id, act_id))
            if len(e): src.error = '; '.join(e)

            e = []
            if not tpkg:
                e.append('no target/package in request %d, action %d; ' % (id, act_id))
            if not prj:
                e.append('no target/project in request %d, action %d; ' % (id, act_id))
            # it is no error, if the target package dies not exist

            subm_id = "SUBMIT(%d):" % id
            plen = max(len(prj),len(tprj))
            print "\n%s %-*s/%s -> %-*s/%s" % (subm_id,
                plen, prj,  pkg,
                plen, tprj, tpkg)
            url = makeurl(opts.apiurl, ['status', "bsrequest?id=%d" % id])
            root = ET.parse(http_GET(url)).getroot()
            if root.attrib.has_key('code'):
                print ET.tostring(root)
                continue
            result = False
            for repo in root.findall('repository'):
                result = result or self._check_repo(repo)
            if result == False:
                print ET.tostring(root)
                continue

            dir = "/work/users/coolo/checker/%s" % str(id)
            if os.path.exists(dir):
                print "%s already exists" % dir
                continue
            os.mkdir(dir)
            os.chdir(dir)
            checkout_package(opts.apiurl, prj, pkg, revision=rev, pathname=dir, server_service_files=True, expand_link=True)
            os.chdir(pkg)
            shutil.rmtree(".osc")
            p = subprocess.Popen("/work/src/bin/check_if_valid_source_dir --batchmode < /dev/null 2>&1", shell=True, stdout=subprocess.PIPE, close_fds=True)
            ret = os.waitpid(p.pid, 0)[1]
            checked = p.stdout.readlines()
            if ret != 0:
                print ''.join(checked)
                continue

            msg="Builds for all Factory repos found"
            if len(checked):
                msg = msg + "\n\nOutput of check script (non-fatal):\n  "
                output = '  '.join(checked)
                msg = msg + output.translate(None, '\033')
                #print msg
                #sys.exit(0)
            self._checker_change_review_state(opts, id, 'accepted', by_group='factory-auto', message=msg)
            print "accepted"

            if cmd == "list":
                pass
            elif cmd == "checker_checkout" or cmd == "co":
                opts.origin = opts.apiurl + '/request/' + str(id) + "\n";
                self._checker_checkout_add(prj, pkg, rev, opts)
            else:
                print "unknown command: %s" % cmd
        else:
            self._checker_change_review_state(opts, id, 'accepted', by_group='factory-auto', message="Unchecked request type %s" % _type)


def do_checker(self, subcmd, opts, *args):
    """${cmd_name}: checker review of submit requests.

    Usage:
      osc checker [OPT] [list] [FILTER|PACKAGE_SRC]
           Shows pending review requests and their current state.

    ${cmd_option_list}
    """

    if len(args) == 0:
        raise oscerr.WrongArgs("Please give a subcommand to 'osc checker' or try 'osc help checker'")

    opts.mode = ''
    opts.verbose = False
    if args[0] == 'auto':     opts.mode = 'auto'
    if args[0] == 'review':   opts.mode = 'both'
    if args[0] == 'co':       opts.mode = 'checker_checkout'
    if args[0] == 'checker_checkout': opts.mode = 'checkout'
    if len(args) > 1 and args[0] in ('auto','manual') and args[1] in ('approve', 'reject'):
        args = args[1:]

    from pprint import pprint

    opts.apiurl = self.get_api_url()

    tmphome = None

    ids = {}
    for a in args:
        if (re.match('\d+', a)):
            ids[a] = 1

    if (not len(ids)):
        # xpath query, using the -m, -r, -s options
        where = "@by_group='factory-auto'+and+@state='new'"

        url = makeurl(opts.apiurl, ['search','request'], "match=state/@name='review'+and+review["+where+"]")
        f = http_GET(url)
        root = ET.parse(f).getroot()
        for rq in root.findall('request'):
            tprj = rq.find('action/target').get('project')
            self._checker_one_request(rq, args[0], opts)
    else:
        # we have a list, use them.
        for id in ids.keys():
            url = makeurl(opts.apiurl, ['request', id])
            f = http_GET(url)
            xml = ET.parse(f)
            root = xml.getroot()
            self._checker_one_request(root, args[0], opts)
