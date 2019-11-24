import re
import time

from urllib.error import HTTPError

import warnings
from xml.etree import cElementTree as ET

from osc.core import change_request_state, show_package_meta, wipebinaries
from osc.core import http_GET, http_PUT, http_DELETE, http_POST
from osc.core import delete_package, search
from osc.core import Request
from osc.util.helper import decode_it
from osclib.core import attribute_value_save
from osclib.core import attribute_value_load
from osclib.core import source_file_load
from osclib.core import source_file_save
from osclib.request_finder import RequestFinder
from datetime import date


class AcceptCommand(object):
    def __init__(self, api):
        self.api = api

    def find_new_requests(self, project):
        match = f"state/@name='new' and action/target/@project='{project}'"
        url = self.api.makeurl(['search', 'request'], { 'match': match })

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

    def accept_all(self, projects, force=False, cleanup=True):
        self.requests = { 'delete': [], 'submit': [] }
        staging_packages = {}

        for prj in projects:
            project = self.api.prj_from_letter(prj)

            status = self.api.project_status(project)
            if status.get('state') != 'acceptable' and not force:
                print('The project "{}" is not yet acceptable.'.format(project))
                #return False

            staging_packages[project] = []
            for request in status.findall('staged_requests/request'):
                self.requests[request.get('type')].append(request.get('package'))
                staging_packages[project].append(request.get('package'))

        other_new = self.find_new_requests(self.api.project)
        for req in other_new:
            self.requests[req['type']].append(req['package'])

        print('delete links to packages pending deletion...')
        self.delete_linked()

        # requests = {'delete': ['AppStream', 'yast2-x11'], 'submit': ['bash']}
        opts = {}
        if force:
            opts['force'] = '1'

        print('triggering staging accepts...')
        for prj in projects:
            project = self.api.prj_from_letter(prj)

            u = self.api.makeurl(['staging', self.api.project, 'staging_projects', project, 'accept'], opts)
            f = http_POST(u)

        for req in other_new:
            print(f"Accepting request {req['id']}: {req['package']}")
            change_request_state(self.api.apiurl, str(req['id']), 'accepted', message='Accept to %s' % self.api.project)

        for prj in projects:
            project = self.api.prj_from_letter(prj)
            print(f'waiting for staging project {project} to be accepted')

            while True:
                status = self.api.project_status(project, reload=True)
                if status.get('state') == 'empty':
                    break
                print('{} requests still staged - waiting'.format(status.find('staged_requests').get('count')))
                time.sleep(1)

            self.api.accept_status_comment(project, staging_packages[project])
            self.api.staging_deactivate(project)

            self.reset_rebuild_data(prj)

            if cleanup:
                self.cleanup(project)

        if self.api.project.startswith('openSUSE:'):
            self.update_factory_version()
            if self.api.item_exists(self.api.crebuild):
                self.sync_buildfailures()

    def cleanup(self, project):
        if not self.api.item_exists(project):
            return False

        pkglist = self.api.list_packages(project)
        clean_list = set(pkglist) - set(self.api.cnocleanup_packages)

        for package in clean_list:
            print("[cleanup] deleted %s/%s" % (project, package))
            delete_package(self.api.apiurl, project, package, force=True, msg="autocleanup")

        return True

    def create_new_links(self, project, pkgname, oldspeclist):
        filelist = self.api.get_filelist_for_package(pkgname=pkgname, project=project, extension='spec')
        removedspecs = set(oldspeclist) - set(filelist)
        for spec in removedspecs:
            # Deleting all the packages that no longer have a .spec file
            url = self.api.makeurl(['source', project, spec[:-5]])
            print("Deleting package %s from project %s" % (spec[:-5], project))
            try:
                http_DELETE(url)
            except HTTPError as err:
                if err.code == 404:
                    # the package link was not yet created, which was likely a mistake from earlier
                    pass
                else:
                    # If the package was there bug could not be delete, raise the error
                    raise

            # Remove package from Rings in case 2nd specfile was removed
            if self.api.ring_packages.get(spec[:-5]):
                delete_package(self.api.apiurl, self.api.ring_packages.get(spec[:-5]), spec[:-5], force=True, msg="Cleanup package in Rings")

        if len(filelist) > 1:
            # There is more than one .spec file in the package; link package containers as needed
            origmeta = source_file_load(self.api.apiurl, project, pkgname, '_meta')
            for specfile in filelist:
                package = specfile[:-5]  # stripping .spec off the filename gives the packagename
                if package == pkgname:
                    # This is the original package and does not need to be linked to itself
                    continue
                # Check if the target package already exists, if it does not, we get a HTTP error 404 to catch
                if not self.api.item_exists(project, package):
                    print("Creating new package %s linked to %s" % (package, pkgname))
                    # new package does not exist. Let's link it with new metadata
                    newmeta = re.sub(r'(<package.*name=.){}'.format(pkgname),
                                     r'\1{}'.format(package),
                                     origmeta)
                    newmeta = re.sub(r'<devel.*>',
                                     r'<devel package="{}"/>'.format(pkgname),
                                     newmeta)
                    newmeta = re.sub(r'<bcntsynctag>.*</bcntsynctag>',
                                     r'',
                                     newmeta)
                    newmeta = re.sub(r'</package>',
                                     r'<bcntsynctag>{}</bcntsynctag></package>'.format(pkgname),
                                     newmeta)
                    source_file_save(self.api.apiurl, project, package, '_meta', newmeta)
                    link = "<link package=\"{}\" cicount=\"copy\" />".format(pkgname)
                    source_file_save(self.api.apiurl, project, package, '_link', link)
        return True

    def update_version_attribute(self, project, version):
        version_attr = attribute_value_load(self.api.apiurl, project, 'ProductVersion')
        if version_attr != version:
            attribute_value_save(self.api.apiurl, project, 'ProductVersion', version)

    def update_factory_version(self):
        """Update project (Factory, 13.2, ...) version if is necessary."""

        # XXX TODO - This method have `factory` in the name.  Can be
        # missleading.

        project = self.api.project
        curr_version = date.today().strftime('%Y%m%d')
        update_version_attr = False
        url = self.api.makeurl(['source', project], {'view': 'productlist'})

        products = ET.parse(http_GET(url)).getroot()
        for product in products.findall('product'):
            product_name = product.get('name') + '.product'
            product_pkg = product.get('originpackage')
            product_spec = source_file_load(self.api.apiurl, project, product_pkg, product_name)
            new_product = re.sub(r'<version>\d{8}</version>', '<version>%s</version>' % curr_version, product_spec)

            if product_spec != new_product:
                update_version_attr = True
                url = self.api.makeurl(['source', project, product_pkg,  product_name])
                http_PUT(url + '?comment=Update+version', data=new_product)

        if update_version_attr:
            self.update_version_attribute(project, curr_version)

        ports_prjs = ['PowerPC', 'ARM', 'zSystems' ]

        for ports in ports_prjs:
            project = self.api.project + ':' + ports
            if self.api.item_exists(project) and update_version_attr:
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
