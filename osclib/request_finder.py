import urllib2
from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import makeurl
from osc.core import http_GET


class RequestFinder:

    @classmethod
    def find_request_id(self, request, apiurl):
        """
        Look up the request by ID to verify if it is correct
        :param request: ID of the added request
        :param apiurl: OBS url
        """

        url = makeurl(apiurl, ['request', str(request)])
        try:
            f = http_GET(url)
        except urllib2.HTTPError:
            return None

        root = ET.parse(f).getroot()

        if root.get('id', None) != request:
            return None

        project = root.find('action').find('target').get('project')
        if project != 'openSUSE:Factory':
            raise oscerr.WrongArgs('Request {} is not for openSUSE:Factory, but for {}'.format(request, project))
        self.srids.add(int(request))
        return True

    @classmethod
    def find_request_package(self, package, apiurl):
        """
        Look up the package by its name and return the SR#
        :param package: name of the package
        :param apiurl: OBS url
        """

        url = makeurl(apiurl, ['request'], 'states=new,review,declined&project=openSUSE:Factory&view=collection&package={}'.format(package))
        f = http_GET(url)
        root = ET.parse(f).getroot()

        ret = None
        for x in root.findall('request'):
            # TODO: check the package matches - OBS is case insensitive
            self.srids.add(int(x.get('id')))
            if ret:
                raise oscerr.WrongArgs('There are multiple requests for package "{}": {}'.format(package, ', '.join(map(str, self.srids))))
            ret = True

        return ret
        
    @classmethod
    def find_request_project(self, source_project, apiurl):
        """
        Look up the source project by its name and return the SR#(s)
        :param source_project: name of the source project
        :param apiurl: OBS url
        """

        url = makeurl(apiurl, ['request'], 'states=new,review&project=openSUSE:Factory&view=collection')
        f = http_GET(url)
        root = ET.parse(f).getroot()

        ret = None
        for rq in root.findall('request'):
            for a in rq.findall('action'):
                s = a.find('source')
                if s is not None and s.get('project') == source_project:
                    self.srids.add(int(rq.attrib['id']))
                    ret = True

        return ret

    @classmethod
    def find_sr(self, pkgs, apiurl):
        """
        Search for all various mutations and return list of SR#s
        :param pkgs: mesh of argumets to search for
        :param apiurl: OBS url
        """

        self.srids = set()
        for p in pkgs:
            if self.find_request_package(p, apiurl):
                continue
            if self.find_request_id(p, apiurl):
                continue
            if self.find_request_project(p, apiurl):
                continue
            raise oscerr.WrongArgs('No SR# found for: {0}'.format(p))
        
        # this is needed in order to ensure we have one level list not nested one
        return sorted(list(self.srids))
