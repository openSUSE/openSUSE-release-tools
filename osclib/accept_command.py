import time
import re
from urllib.error import HTTPError

from lxml import etree as ET

from osc.core import change_request_state
from osc.core import http_GET, http_POST
from osc.core import delete_package, meta_get_packagelist
from osc import conf
from osclib.core import attribute_value_save, attribute_value_load
from osclib.core import source_file_load, source_file_save
from osclib.core import create_set_bugowner_request
from osclib.pkglistgen_comments import PkglistComments
from datetime import date


class AcceptCommand(object):
    def __init__(self, api):
        self.api = api
        self.config = conf.config[self.api.project]
        self.pkglist_comments = PkglistComments(self.api.apiurl)

    def find_new_requests(self, project):
        match = f"state/@name='new' and action/target/@project='{project}'"
        url = self.api.makeurl(['search', 'request'], {'match': match})

        f = http_GET(url)
        root = ET.parse(f).getroot()

        rqs = []
        for rq in root.findall('request'):
            for action in rq.findall('action'):
                for t in action.findall('target'):
                    rqs.append({'id': int(rq.get('id')),
                                'package': str(t.get('package')),
                                'type': action.get('type')})
                    break
        return rqs

    def reset_rebuild_data(self, project):
        data = self.api.pseudometa_file_load('support_pkg_rebuild')
        if data is None:
            return

        root = ET.fromstring(data)
        for stg in root.findall('staging'):
            if stg.get('name') == project:
                stg.find('rebuild').text = 'unknown'
                stg.find('supportpkg').text = ''

        # reset accepted staging project rebuild state to unknown and clean up
        # supportpkg list
        content = ET.tostring(root)
        if content != data:
            self.api.pseudometa_file_save('support_pkg_rebuild', content, 'accept command update')

    def delete_linked(self):
        for package in self.requests['delete']:
            for link in self.api.linked_packages(package):
                if link['project'] in self.api.rings or link['project'] == self.api.project:
                    print(f"delete {link['project']}/{link['package']}")
                    delete_package(self.api.apiurl, link['project'], link['package'],
                                   msg="remove link while accepting delete of {}".format(package))

    def check_request_for_bugowner(self, to_request, package, id):
        url = self.api.makeurl(['request', str(id)])

        f = http_GET(url)
        root = ET.parse(f).getroot()
        description = root.find('description')
        if description is None or description.text is None:
            return
        description = description.text
        for line in description.splitlines():
            # this relies on the format relied upon in check_bugowner
            m = re.search(r'^bugowner:\s*(\S*)', line)
            if not m:
                continue
            to_request[package] = {'id': id, 'bugowner': m.group(1)}
            return

    def accept_all(self, projects, force=False, cleanup=True):
        accept_all_green = len(projects) == 0
        if accept_all_green:
            print('Accepting all acceptable projects')
            if force:
                print('ERROR: Not compatible with force option')
                return False

        self.requests = {'delete': [], 'submit': []}
        staging_packages = {}

        if accept_all_green:
            projects = self.api.get_staging_projects()

        bugowners_to_request = dict()
        for prj in projects:
            project = self.api.prj_from_letter(prj)

            status = self.api.project_status(project)
            if status.get('state') != 'acceptable':
                if accept_all_green:
                    continue
                if not force:
                    print('The project "{}" is not yet acceptable.'.format(project))
                    return False

            staging_packages[project] = []
            for request in status.findall('staged_requests/request'):
                type = request.get('type')
                if type in self.requests:
                    self.requests[type].append(request.get('package'))
                if type == 'submit':
                    self.check_request_for_bugowner(bugowners_to_request, request.get('package'), request.get('id'))
                staging_packages[project].append(request.get('package'))

        other_new = self.find_new_requests(self.api.project)
        for req in other_new:
            if req['type'] in self.requests:
                self.requests[req['type']].append(req['package'])
            if req['type'] == 'submit':
                self.check_request_for_bugowner(bugowners_to_request, req['package'], req['id'])

        print('delete links to packages pending deletion...')
        self.delete_linked()

        # we have checked ourselves and accepting one staging project creates a race
        # for the other staging projects to appear building again
        opts = {'force': '1'}

        print('triggering staging accepts...')
        for project in staging_packages.keys():
            u = self.api.makeurl(['staging', self.api.project, 'staging_projects', project, 'accept'], opts)
            http_POST(u)
            self.api.switch_flag_in_prj(project, flag='build', state='disable', repository='images')

        for req in other_new:
            print(f"Accepting request {req['id']}: {req['package']}")
            change_request_state(self.api.apiurl, str(req['id']), 'accepted', message='Accept to %s' % self.api.project)

        for project in sorted(staging_packages.keys()):
            print(f'waiting for staging project {project} to be accepted')

            while True:
                status = self.api.project_status(project, reload=True)
                if status.get('state') == 'empty':
                    break
                print('{} requests still staged - waiting'.format(status.find('staged_requests').get('count')))
                time.sleep(1)

            self.api.accept_status_comment(project, staging_packages[project])
            if self.api.is_adi_project(project):
                self.api.delete_empty_adi_project(project)
                continue

            self.pkglist_comments.check_staging_accept(project, self.api.project)
            self.api.staging_deactivate(project)
            self.reset_rebuild_data(project)

            if cleanup:
                self.cleanup(project)

        for package in self.requests['submit']:
            self.fix_linking_packages(package)
            if package in bugowners_to_request:
                infos = bugowners_to_request[package]
                id = infos['id']
                message = f"Bugowner info derived from request {id}"
                create_set_bugowner_request(self.api.apiurl, self.api.project, infos['bugowner'],
                                            target_package=package, message=message)

        if self.api.project.startswith('openSUSE:'):
            self.update_factory_version()
            if self.api.crebuild and self.api.item_exists(self.api.crebuild):
                self.sync_buildfailures()

        return True

    def cleanup(self, project):
        if not self.api.item_exists(project):
            return

        pkglist = self.api.list_packages(project)
        clean_list = set(pkglist) - set(self.api.cnocleanup_packages)

        for package in clean_list:
            print("[cleanup] deleted %s/%s" % (project, package))
            delete_package(self.api.apiurl, project, package, force=True, msg="autocleanup")

        return

    def check_local_links(self):
        for package in meta_get_packagelist(self.api.apiurl, self.api.project):
            self.fix_linking_packages(package, True)

    def fix_linking_packages(self, package, dry=False):
        project = self.api.project
        file_list = self.api.get_filelist_for_package(package, project)
        # ignore linked packages
        if '_link' in file_list:
            return
        needed_links = set()
        # if there's a multibuild we assume all flavors are built
        # using multibuild. So any potential previous links have to
        # be removed ie set of needed_links left empty.
        if '_multibuild' not in file_list:
            for file in file_list:
                if file.endswith('.spec') and file != f'{package}.spec':
                    needed_links.add(file[:-5])
        local_links = set()
        for link in self.api.linked_packages(package):
            if link['project'] == project:
                local_links.add(link['package'])

        # Deleting all the packages that no longer have a .spec file
        for link in local_links - needed_links:
            print(f"Deleting package {project}/{link}")
            if dry:
                continue
            try:
                delete_package(self.api.apiurl, project, link, msg=f"No longer linking to {package}")
            except HTTPError as err:
                if err.code == 404:
                    # the package link was not yet created, which was likely a mistake from earlier
                    pass
                else:
                    # If the package was there bug could not be delete, raise the error
                    raise

            # Remove package from Rings in case 2nd specfile was removed
            if self.api.ring_packages.get(link):
                delete_package(self.api.apiurl, self.api.ring_packages.get(
                    link), link, force=True, msg="Cleanup package in Rings")

        for link in needed_links - local_links:
            # There is more than one .spec file in the package; link package containers as needed
            meta = ET.fromstring(source_file_load(self.api.apiurl, project, package, '_meta'))
            print(f"Creating new link {link}->{package}")
            if dry:
                continue

            meta.attrib['name'] = link
            bcnt = meta.find('bcntsynctag')
            if bcnt is None:
                bcnt = ET.SubElement(meta, 'bcntsynctag')
            bcnt.text = package
            devel = meta.find('devel')
            if devel is None:
                devel = ET.SubElement(meta, 'devel')
            devel.attrib['project'] = project
            devel.attrib['package'] = package

            source_file_save(self.api.apiurl, project, link, '_meta', ET.tostring(meta))
            xml = f"<link package='{package}' cicount='copy' />"
            source_file_save(self.api.apiurl, project, link, '_link', xml)

    def update_version_attribute(self, project, version):
        version_attr = attribute_value_load(self.api.apiurl, project, 'ProductVersion')
        if version_attr != version:
            attribute_value_save(self.api.apiurl, project, 'ProductVersion', version)

    def update_factory_version(self):
        """Update project (Factory, 13.2, ...) version if is necessary."""

        # XXX TODO - This method have `factory` in the name.  Can be
        # missleading.

        project = self.api.project
        pred_productversion = self.config.get('always_set_productversion_to', '')
        if pred_productversion:
            curr_version = pred_productversion
        else:
            curr_version = date.today().strftime('%Y%m%d')

        self.update_version_attribute(project, curr_version)

        ports_prjs = ['PowerPC', 'ARM', 'zSystems', 'RISCV']
        for ports in ports_prjs:
            project = self.api.project + ':' + ports
            if self.api.item_exists(project):
                self.update_version_attribute(project, curr_version)

    def sync_buildfailures(self):
        """
        Trigger rebuild of packages that failed build in either
        openSUSE:Factory or openSUSE:Factory:Rebuild, but not the
        other Helps over the fact that openSUSE:Factory uses
        rebuild=local, thus sometimes 'hiding' build failures.
        """

        for arch in ["x86_64", "i586"]:
            fact_result = self.api.get_prj_results(self.api.project, arch)
            fact_result = self.api.check_pkgs(fact_result)
            rebuild_result = self.api.get_prj_results(self.api.crebuild, arch)
            rebuild_result = self.api.check_pkgs(rebuild_result)
            result = set(rebuild_result) ^ set(fact_result)

            print(sorted(result))

            for package in result:
                self.api.rebuild_pkg(package, self.api.project, arch, None)
                self.api.rebuild_pkg(package, self.api.crebuild, arch, None)
