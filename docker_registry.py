#!/usr/bin/python3
#
# Copyright (c) 2018 SUSE LLC
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# This is a very basic client for the Docker Registry V2 API.
# It exists for a single reason: All clients either:
# - Don't work
# - Don't support uploading
# - Don't support multi-arch images (manifest lists)
# and some even all three.

import hashlib
import json
import os
import urllib.parse
import requests


class DockerRegistryClient():
    def __init__(self, url, username, password, repository):
        self.url = url
        self.username = username
        self.password = password
        self.repository = repository
        self.scopes = ["repository:%s:pull,push,delete" % repository]
        self.token = None

    class DockerRegistryError(Exception):
        """Some nicer display of docker registry errors"""
        def __init__(self, errors):
            self.errors = errors

        def __str__(self):
            ret = "Docker Registry errors:"
            for error in self.errors:
                ret += "\n" + str(error)

            return ret

    def _updateToken(self, www_authenticate):
        bearer_parts = www_authenticate[len("Bearer "):].split(",")
        bearer_dict = {}
        for part in bearer_parts:
            assignment = part.split('=')
            bearer_dict[assignment[0]] = assignment[1].strip('"')

        scope_param = "&scope=".join([""] + [urllib.parse.quote(scope) for scope in self.scopes])
        response = requests.get("%s?service=%s%s" % (bearer_dict['realm'], bearer_dict['service'], scope_param),
                                auth=(self.username, self.password))
        self.token = response.json()['token']

    def doHttpCall(self, method, url, **kwargs):
        """This method wraps the requested method from the requests module to
        add the token for authorization."""
        try_update_token = True

        # Relative to the host
        if url.startswith("/"):
            url = self.url + url

        if "headers" not in kwargs:
            kwargs['headers'] = {}

        while True:
            resp = None
            if self.token is not None:
                kwargs['headers']['Authorization'] = "Bearer " + self.token

            methods = {'POST': requests.post,
                       'GET': requests.get,
                       'HEAD': requests.head,
                       'PUT': requests.put,
                       'DELETE': requests.delete}

            if method not in methods:
                return False

            resp = methods[method](url, **kwargs)

            if resp.status_code == 401 or resp.status_code == 403:
                if try_update_token:
                    try_update_token = False
                    self._updateToken(resp.headers['Www-Authenticate'])
                    continue

            if resp.status_code > 400 and resp.status_code < 404:
                try:
                    errors = resp.json()['errors']
                    raise self.DockerRegistryError(errors)
                except ValueError:
                    pass

            return resp

    def uploadManifest(self, content, reference=None):
        """Upload a manifest. Data is given as bytes in content, the digest/tag in reference.
        If reference is None, the digest is computed and used as reference.
        On success, the used reference is returned. False otherwise."""
        content_json = json.loads(content.decode('utf-8'))
        if "mediaType" not in content_json:
            raise Exception("Invalid manifest")

        if reference is None:
            alg = hashlib.sha256()
            alg.update(content)
            reference = "sha256:" + alg.hexdigest()

        resp = self.doHttpCall("PUT", "/v2/%s/manifests/%s" % (self.repository, reference),
                               headers={'Content-Type': content_json['mediaType']},
                               data=content)

        if resp.status_code != 201:
            return False

        return reference

    def uploadManifestFile(self, filename, reference=None):
        """Upload a manifest. If the filename doesn't equal the digest, it's computed.
        If reference is None, the digest is used. You can use the manifest's tag
        for example.
        On success, the used reference is returned. False otherwise."""
        with open(filename, "rb") as manifest:
            content = manifest.read()

            if reference is None:
                basename = os.path.basename(filename)
                if basename.startswith("sha256:"):
                    reference = basename

            if reference is None:
                raise Exception("No reference determined")

            return self.uploadManifest(content, reference)

    def getManifest(self, reference):
        """Get a (json-parsed) manifest with the given reference (digest or tag).
        If the manifest does not exist, return None. For other errors, False."""
        resp = self.doHttpCall("GET", "/v2/%s/manifests/%s" % (self.repository, reference),
                               headers={'Accept': "application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json"})  # noqa: E501

        if resp.status_code == 404:
            return None

        if resp.status_code != 200:
            return False

        return resp.json()

    def getManifestDigest(self, reference):
        """Return the digest of the manifest with the given reference.
        If the manifest doesn't exist or the request fails, it returns False."""
        resp = self.doHttpCall("HEAD", "/v2/%s/manifests/%s" % (self.repository, reference),
                               headers={'Accept': "application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json"})  # noqa: E501

        if resp.status_code != 200:
            return False

        return resp.headers['Docker-Content-Digest']

    def deleteManifest(self, digest):
        """Delete the manifest with the given reference."""
        resp = self.doHttpCall("DELETE", "/v2/%s/manifests/%s" % (self.repository, digest))

        return resp.status_code == 202

    def uploadBlob(self, filename, digest=None):
        """Upload the blob with the given filename and digest. If digest is None,
        the basename has to equal the digest.
        Returns True if blob already exists or upload succeeded."""

        if digest is None:
            digest = os.path.basename(filename)

        if not digest.startswith("sha256:"):
            raise Exception("Invalid digest")

        # Check whether the blob already exists - don't upload it needlessly.
        stat_request = self.doHttpCall("HEAD", "/v2/%s/blobs/%s" % (self.repository, digest))
        if stat_request.status_code == 200 or stat_request.status_code == 307:
            return True

        # For now we can do a single upload call with everything inlined
        # (which also means completely in ram, but currently it's never > 50 MiB)
        content = None
        with open(filename, "rb") as blob:
            content = blob.read()

        # First request an upload "slot", we get an URL we can PUT to back
        upload_request = self.doHttpCall("POST", "/v2/%s/blobs/uploads/" % self.repository)
        if upload_request.status_code == 202:
            location = upload_request.headers['Location']
            upload = self.doHttpCall("PUT", location + "&digest=" + digest,
                                     data=content)
            return upload.status_code == 201

        return False
