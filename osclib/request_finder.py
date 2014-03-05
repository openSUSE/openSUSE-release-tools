import urllib2
from xml.etree import cElementTree as ET

from osc import oscerr
from osc.core import makeurl
from osc.core import http_GET


FACTORY = 'openSUSE:Factory'
STG_PREFIX = 'openSUSE:Factory:Staging:'


class RequestFinder:

    def __init__(self, apiurl, stagingapi):
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
        self.stagingapi = stagingapi
        self.srs = {}

    def _filter_review_by_project(self, element, state):
        reviews = [r.get('by_project')
                   for r in element.findall('review')
                   if r.get('by_project') and r.get('state') == state]
        return reviews

    def _new_review_by_project(self, request, element):
        reviews = self._filter_review_by_project(element, 'new')
        assert len(reviews) <= 1, 'Request "{}" have more than one review by project "{}"'.format(request,
                                                                                                  reviews)
        return reviews[0] if reviews else None

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

        review = self._new_review_by_project(request, root)
        if review:
            self.srs[int(request)]['staging'] = review

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
            state = sr.find('state').get('name')

            self.srs[request] = {'project': 'openSUSE:Factory', 'state': state }

            review = self._new_review_by_project(request, sr)
            if review:
                self.srs[int(request)]['staging'] = review

            if ret:
                if self.srs[ret]['state'] == 'declined':
                    # ignore previous requests if they are declined
                    # if they are the last one, it's fine to return them
                    del self.srs[ret]
                else:
                    msg = 'There are multiple requests for package "{}": {} and {}'
                    msg = msg.format(package, ret, request)
                    raise oscerr.WrongArgs(msg)
            ret = request

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
                    review = self._new_review_by_project(request, sr)
                    if review:
                        self.srs[int(request)]['staging'] = review
                    ret = True

        return ret

    def find(self, pkgs):
        """
        Search for all various mutations and return list of SR#s
        :param pkgs: mesh of argumets to search for

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

    def find_via_stagingapi(self, pkgs):
        """
        Search for all various mutations and return list of SR#s. Use
        and instance of StagingAPI to direct the search, this makes
        sure that the SR# are inside a staging project.
        :param pkgs: mesh of argumets to search for

        This function is only called for its side effect.
        """
        def _is_int(x):
            return isinstance(x, int) or x.isdigit()

        for p in pkgs:
            found = False
            for staging in self.stagingapi.get_staging_projects():
                if _is_int(p) and self.stagingapi.get_package_for_request_id(staging, p):
                    self.srs[int(p)] = {'staging': staging}
                    found = True
                    continue
                else:
                    rq = self.stagingapi.get_request_id_for_package(staging, p)
                    if rq:
                        self.srs[rq] = {'staging': staging}
                        found = True
                        continue
            if not found:
                raise oscerr.WrongArgs('No SR# found for: {}'.format(p))

    @classmethod
    def find_sr(cls, pkgs, apiurl, stagingapi=None):
        """
        Search for all various mutations and return list of SR#s
        :param pkgs: mesh of argumets to search for
        :param apiurl: OBS url
        """
        finder = cls(apiurl, stagingapi)
        finder.find(pkgs)
        return finder.srs

    @classmethod
    def find_staged_sr(cls, pkgs, apiurl, stagingapi):
        """
        Search for all various mutations and return a single SR#s.
        :param pkgs: mesh of argumets to search for (SR#|package name)
        :param apiurl: OBS url
        :param stagingapi: StagingAPI instance
        """
        finder = cls(apiurl, stagingapi)
        finder.find_via_stagingapi(pkgs)
        return finder.srs
