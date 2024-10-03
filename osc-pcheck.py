# Copyright (C) 2015 SUSE Linux Products GmbH
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

from osc import cmdln
import osc.core


@cmdln.option('--push', action='store_true',
              help="Push changed packages to their parents")
@cmdln.option('-m', "--message",
              help='Specify submit message')
def do_pcheck(self, subcmd, opts, project):
    """${cmd_name}: Show changed packages (packages that have a diff)

    Examples:
    osc pcheck <prj>        # shows changed packages etc. for <prj>

    --push      Create submit requests for packages with a diff (if none exists yet)
    -m          Specify submit message (defaut: "Scripted push of project <prj>")

    """
    apiurl = self.get_api_url()
    sinfos = osc.core.get_project_sourceinfo(apiurl, project, True)
    todo = {}
    errors = {}
    md5s = {}
    pmap = {}
    changed = []
    changeSRed = {}
    api = oscapi(apiurl)
    for pkg, sinfo in sinfos.items():
        if sinfo.find('error'):
            errors[pkg] = sinfo.find('error').text
            continue
        elif sinfo.find('originpackage') is not None:
            # This is a package created from a _multibuild
            # Status will be checked by the main one (which
            # has no originpackage.) so let's not continue further
            continue
        elif sinfo.find('linked') is not None:
            elm = sinfo.find('linked')
            key = f"{elm.get('project')}/{elm.get('package')}"
            pmap.setdefault(key, []).append(pkg)
            todo.setdefault(elm.get('project'), []).append(elm.get('package'))
        md5s[pkg] = sinfo.get('verifymd5')
    for prj, pkgs in todo.items():
        sinfos = osc.core.get_project_sourceinfo(apiurl, prj, True, *pkgs)
        for pkg, sinfo in sinfos.items():
            key = f'{prj}/{pkg}'
            for p in pmap[key]:
                vmd5 = md5s.pop(p)
                if vmd5 == sinfo.get('verifymd5'):
                    continue
                # Is there already an SR outgoing for this package?
                SRid = int(api.sr_for_package(project, p))
                if SRid > 0:
                    changeSRed[p] = SRid
                else:
                    changed.append(p)
                    if opts.push:
                        if opts.message:
                            message = opts.message
                        else:
                            message = f"Scripted push from {project}"
                        api.create(project=project, package=p, target=prj, message=message)

    overview = f'Overview of project {project}'
    print()
    print(overview)
    print('=' * len(overview))
    print(f'Changed & unsubmitted packages: {len(changed)}')
    print(', '.join(changed))
    print()
    print(f'Changed & submitted packages: {len(changeSRed.keys())}')
    print(', '.join([f'{pkg}({SR})' for pkg, SR in changeSRed.items()]))
    print()
    print(f'Packages without link: {len(md5s.keys())}')
    print(', '.join(md5s.keys()))
    print()
    print(f'Packages with errors: {len(errors.keys())}')
    print('\n'.join([f'{p}: {err}' for p, err in errors.items()]))


class oscapi:
    def __init__(self, apiurl):
        self.apiurl = apiurl

    def sr_for_package(self, project, package):
        query = "(state/@name='new' or state/@name='review') and " \
                "(action/source/@project='{project}' or submit/source/@project='{project}') and " \
                "(action/source/@package='{package}' or submit/source/@package='Packafe')".format(project=project, package=package)
        result = osc.core.search(self.apiurl, request=query)
        collection = result['request']
        for root in collection.findall('request'):
            return root.get('id')
        return 0

    def create(self, project, package, target, message):
        currev = osc.core.get_source_rev(self.apiurl, project, package)['rev']
        print(f"Creating a request from {project}/{package}")
        query = {'cmd': 'create'}
        url = osc.core.makeurl(self.apiurl, ['request'], query=query)

        data = '<request><action type="submit"><source project="{project}" package="{package}" rev="{rev}"/>' \
               '<target project="{target}" package="{package}"/></action><state name="new"/><description>{message}</description>' \
               '</request>'.format(project=project, package=package, target=target, rev=currev, message=message)
        osc.core.http_POST(url, data=data)
