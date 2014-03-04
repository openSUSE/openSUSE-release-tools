import urllib2
from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import makeurl
from osc.core import http_GET


FACTORY = 'openSUSE:Factory'
STG_PREFIX = 'openSUSE:Factory:Staging:'


class RequestFinder:

    def __init__(self, apiurl):
        # Store the list of submit request together with the source project
        #
        # Example:
        #
        #     {
        #         212454: {
        #             'project': 'openSUSE:Factory',
        #         },
        #
        #         223870: {
        #             'project': 'openSUSE:Factory',
        #             'staging': 'openSUSE:Factory:Staging:A',
        #         }
        #     }
        #
        self.apiurl = apiurl
        self.srs = {}

    def find_request_id(self, request):
        """
        Look up the request by ID to verify if it is correct
        :param request: ID of the added request
        :param apiurl: OBS url
        """

        url = makeurl(self.apiurl, ['request', str(request)])
        try:
            f = http_GET(url)
        except urllib2.HTTPError:
            return None

        root = ET.parse(f).getroot()

        if root.get('id', None) != request:
            return None

        project = root.find('action').find('target').get('project')
        if project != FACTORY and not project.startswith(STG_PREFIX):
            msg = 'Request {} is not for openSUSE:Factory, but for {}'
            msg = msg.format(request, project)
            raise oscerr.WrongArgs(msg)
        self.srs[int(request)] = {'project': project}

        for review in root.findall('review'):
            if review.get('by_project'):
                self.srs[int(request)]['staging'] = review.get('by_project')
                break

        return True

    def find_request_package(self, package):
        """
        Look up the package by its name and return the SR#
        :param package: name of the package
        :param apiurl: OBS url
        """

        query = 'states=new,review,declined&project=openSUSE:Factory&view=collection&package={}'
        query = query.format(package)
        url = makeurl(self.apiurl, ['request'], query)
        f = http_GET(url)

        root = ET.parse(f).getroot()

        ret = None
        for sr in root.findall('request'):
            # TODO: check the package matches - OBS is case insensitive
            request = int(sr.get('id'))
            self.srs[request] = {'project': 'openSUSE:Factory'}
            for review in sr.findall('review'):
                if review.get('by_project'):
                    self.srs[request]['staging'] = review.get('by_project')
                    break
            if ret:
                msg = 'There are multiple requests for package "{}": {}'
                msg = msg.format(package, ', '.join(map(str, self.srs.keys())))
                raise oscerr.WrongArgs(msg)
            ret = True

        return ret

    def find_request_project(self, source_project):
        """
        Look up the source project by its name and return the SR#(s)
        :param source_project: name of the source project
        :param apiurl: OBS url
        """

        query = 'states=new,review&project=openSUSE:Factory&view=collection'
        url = makeurl(self.apiurl, ['request'], query)
        f = http_GET(url)
        root = ET.parse(f).getroot()

        ret = None
        for sr in root.findall('request'):
            for act in sr.findall('action'):
                src = act.find('source')
                if src is not None and src.get('project') == source_project:
                    request = int(sr.attrib['id'])
                    self.srs[request] = {'project': 'openSUSE:Factory'}
                    for review in sr.findall('review'):
                        if review.get('by_project'):
                            self.srs[request]['staging'] = review.get('by_project')
                            break
                    ret = True

        return ret

    def find(self, pkgs, include_project=True):
        """
        Search for all various mutations and return list of SR#s
        :param pkgs: mesh of argumets to search for
        :param include_project: if True, include the search or request
           inside a project

        This function is only called for its side effect.
        """
        for p in pkgs:
            if self.find_request_package(p):
                continue
            if self.find_request_id(p):
                continue
            if self.find_request_project(p):
                continue
            raise oscerr.WrongArgs('No SR# found for: {}'.format(p))

    @classmethod
    def find_sr(cls, pkgs, apiurl):
        """
        Search for all various mutations and return list of SR#s
        :param pkgs: mesh of argumets to search for
        :param apiurl: OBS url
        """
        finder = cls(apiurl)
        finder.find(pkgs)
        return finder.srs

    @classmethod
    def find_single_sr(cls, pkg, apiurl):
        """
        Search for all various mutations and return a single SR#s.
        :param pkg: a single SR|package to search
        :param apiurl: OBS url
        """
        finder = cls(apiurl)
        finder.find([pkg], include_project=False)
        assert len(finder.srs) <= 1, 'Found more that one submit request'
        return finder.srs.items()[0]
