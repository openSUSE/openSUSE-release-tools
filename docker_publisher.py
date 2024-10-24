#!/usr/bin/python3
#
# Copyright (c) 2022 SUSE LLC
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

# This script's job is to listen for new releases of products with docker images
# and publish those.

import argparse
import json
import os
import re
import requests
import subprocess
import sys
import tempfile
from lxml import etree as xml

import docker_registry

REPOMD_NAMESPACES = {'md': "http://linux.duke.edu/metadata/common",
                     'repo': "http://linux.duke.edu/metadata/repo",
                     'rpm': "http://linux.duke.edu/metadata/rpm"}


class DockerImagePublisher:
    """Base class for handling the publishing of docker images.
    This handles multiple architectures, which have different layers
    and therefore versions."""

    def releasedDockerImageVersion(self, arch):
        """This function returns an identifier for the released docker
        image's version."""
        raise Exception("pure virtual")

    def prepareReleasing(self):
        """Prepare the environment to allow calls to releaseDockerImage."""
        raise Exception("pure virtual")

    def addImage(self, version, arch, image_path):
        """This function adds the docker image with the image manifest, config layers
        in image_path."""
        raise Exception("pure virtual")

    def finishReleasing(self):
        """This function publishes the released layers."""
        raise Exception("pure virtual")


class DockerPublishException(Exception):
    pass


class DockerImageFetcher:
    """Base class for handling the acquiring of docker images."""

    def currentVersion(self):
        """This function returns the version of the latest available version
        of the image for the product."""
        raise Exception("pure virtual")

    def getDockerImage(self, callback):
        """This function downloads the root fs layer and calls callback
        with its path as argument."""
        raise Exception("pure virtual")


class DockerFetchException(Exception):
    pass


class DockerImagePublisherRegistry(DockerImagePublisher):
    """The DockerImagePublisherRegistry class works by using a manifest list to
    describe a tag. The list contains a manifest for each architecture.
    The manifest will be edited instead of replaced, which means if you don't
    call addImage for an architecture, the existing released image stays in place."""
    MAP_ARCH_RPM_DOCKER = {'i586': ("386", None),
                           'x86_64': ("amd64", None),
                           'armv6l': ("arm", "v6"),
                           'armv7l': ("arm", "v7"),
                           'aarch64': ("arm64", "v8"),
                           'ppc64le': ("ppc64le", None),
                           's390x': ("s390x", None),
                           'riscv64': ("riscv64", None)}

    def __init__(self, dhc, tag, aliases=[]):
        """Construct a DIPR by passing a DockerRegistryClient instance as dhc
        and a name for a tag as tag.
        Optionally, add tag aliases as aliases. Those will only be written to,
        never read."""
        self.dhc = dhc
        self.tag = tag
        self.aliases = aliases
        # The manifestlist for the tag is only downloaded if this cache is empty,
        # so needs to be set to None to force a redownload.
        self.cached_manifestlist = None
        # Construct a new manifestlist for the tag.
        self.new_manifestlist = None

    def getDockerArch(self, arch):
        if arch not in self.MAP_ARCH_RPM_DOCKER:
            raise DockerPublishException(f"Unknown arch {arch}")

        return self.MAP_ARCH_RPM_DOCKER[arch]

    def _getManifestlist(self):
        if self.cached_manifestlist is None:
            self.cached_manifestlist = self.dhc.getManifest(self.tag)

        return self.cached_manifestlist

    def _manifestIsForArch(self, manifest, docker_arch, docker_variant):
        if 'variant' in manifest['platform'] and manifest['platform']['variant'] != docker_variant:
            return False

        return manifest['platform']['architecture'] == docker_arch

    def releasedDockerImageVersion(self, arch):
        docker_arch, docker_variant = self.getDockerArch(arch)

        manifestlist = self._getManifestlist()

        if manifestlist is None:
            # No manifest
            return None

        for manifest in manifestlist['manifests']:
            if docker_variant is not None:
                if 'variant' not in manifest['platform'] or manifest['platform']['variant'] != docker_variant:
                    continue

            if manifest['platform']['architecture'] == docker_arch:
                if 'vnd-opensuse-version' in manifest:
                    return manifest['vnd-opensuse-version']

        return None

    def prepareReleasing(self):
        if self.new_manifestlist is not None:
            raise DockerPublishException("Did not finish publishing")

        self.new_manifestlist = self._getManifestlist()

        # Generate an empty manifestlist
        if not self.new_manifestlist:
            self.new_manifestlist = {'schemaVersion': 2,
                                     'tag': self.tag,
                                     'mediaType': "application/vnd.docker.distribution.manifest.list.v2+json",
                                     'manifests': []}

        return True

    def getV2ManifestEntry(self, path, filename, mediaType):
        """For V1 -> V2 schema conversion. filename has to contain the digest"""
        digest = filename

        if re.match(r"^[a-f0-9]{64}", digest):
            digest = "sha256:" + os.path.splitext(digest)[0]

        if not digest.startswith("sha256"):
            raise DockerPublishException("Invalid manifest contents")

        return {'mediaType': mediaType,
                'size': os.path.getsize(path + "/" + filename),
                'digest': digest,
                'x-osdp-filename': filename}

    def convertV1ToV2Manifest(self, path, manifest_v1):
        """Converts the v1 manifest in manifest_v1 to a V2 manifest and returns it"""

        layers = []
        # The order of layers changed in V1 -> V2
        for layer_filename in manifest_v1['Layers'][::-1]:
            layers += [self.getV2ManifestEntry(path, layer_filename,
                                               "application/vnd.docker.image.rootfs.diff.tar.gzip")]

        return {'schemaVersion': 2,
                'mediaType': "application/vnd.docker.distribution.manifest.v2+json",
                'config': self.getV2ManifestEntry(path, manifest_v1['Config'],
                                                  "application/vnd.docker.container.image.v1+json"),
                'layers': layers}

    def removeImage(self, arch):
        docker_arch, docker_variant = self.getDockerArch(arch)

        self.new_manifestlist['manifests'] = [m for m in self.new_manifestlist['manifests']
                                              if not self._manifestIsForArch(m, docker_arch, docker_variant)]

    def addImage(self, version, arch, image_path):
        docker_arch, docker_variant = self.getDockerArch(arch)

        manifest = None

        with open(image_path + "/manifest.json") as manifest_file:
            manifest = json.load(manifest_file)

        manifest_v2 = self.convertV1ToV2Manifest(image_path, manifest[0])
        # Upload blobs
        if not self.dhc.uploadBlob(image_path + "/" + manifest_v2['config']['x-osdp-filename'],
                                   manifest_v2['config']['digest']):
            raise DockerPublishException("Could not upload the image config")

        for layer in manifest_v2['layers']:
            if not self.dhc.uploadBlob(image_path + "/" + layer['x-osdp-filename'],
                                       layer['digest']):
                raise DockerPublishException("Could not upload an image layer")

        # Upload the manifest
        manifest_content = json.dumps(manifest_v2).encode("utf-8")
        manifest_digest = self.dhc.uploadManifest(manifest_content)

        if manifest_digest is False:
            raise DockerPublishException("Could not upload the manifest")

        # Register the manifest in the list
        replaced = False
        for manifest in self.new_manifestlist['manifests']:
            if not self._manifestIsForArch(manifest, docker_arch, docker_variant):
                continue

            manifest['mediaType'] = manifest_v2['mediaType']
            manifest['size'] = len(manifest_content)
            manifest['digest'] = manifest_digest
            manifest['vnd-opensuse-version'] = version
            if docker_variant is not None:
                manifest['platform']['variant'] = docker_variant

            replaced = True

        if not replaced:
            # Add it instead
            manifest = {'mediaType': manifest_v2['mediaType'],
                        'size': len(manifest_content),
                        'digest': manifest_digest,
                        'vnd-opensuse-version': version,
                        'platform': {
                            'architecture': docker_arch,
                            'os': "linux"}
                        }
            if docker_variant is not None:
                manifest['platform']['variant'] = docker_variant

            self.new_manifestlist['manifests'] += [manifest]

        return True

    def finishReleasing(self):
        # Generate the manifest content
        manifestlist_content = json.dumps(self.new_manifestlist).encode('utf-8')

        # Push the aliases
        for alias in self.aliases:
            if not self.dhc.uploadManifest(manifestlist_content, alias):
                raise DockerPublishException("Could not push an manifest list alias")

        # Push the new manifest list
        if not self.dhc.uploadManifest(manifestlist_content, self.tag):
            raise DockerPublishException("Could not upload the new manifest list")

        self.new_manifestlist = None
        self.cached_manifestlist = None  # force redownload

        return True


class DockerImageFetcherURL(DockerImageFetcher):
    """A trivial implementation. It downloads a (compressed) tar archive and passes
    the decompressed contents to the callback.
    The version number can't be determined automatically (it would need to extract
    the image and look at /etc/os-release each time - too expensive.) so it
    has to be passed manually."""
    def __init__(self, version, url):
        self.version = version
        self.url = url

    def currentVersion(self):
        return self.version

    def getDockerImage(self, callback):
        """Download the tar and extract it"""
        with tempfile.NamedTemporaryFile() as tar_file:
            tar_file.write(requests.get(self.url).content)
            with tempfile.TemporaryDirectory() as tar_dir:
                # Extract the .tar.xz into the dir
                subprocess.call(f"tar -xaf '{tar_file.name}' -C '{tar_dir}'", shell=True)
                return callback(tar_dir)


class DockerImageFetcherOBS(DockerImageFetcher):
    """Uses the OBS API to access the build artifacts.
    Url has to be https://build.opensuse.org/public/build/<project>/<repo>/<arch>/<pkgname>
    If maintenance_release is True, it picks the buildcontainer released last with that name.
    e.g. for "foo" it would pick "foo.2019" instead of "foo" or "foo.2018"."""
    def __init__(self, url, maintenance_release=False):
        self.url = url
        self.newest_release_url = None
        if not maintenance_release:
            self.newest_release_url = url

    def _isMaintenanceReleaseOf(self, release, source):
        """Returns whether release describes a maintenance release of source.
        E.g. "foo.2019", "foo" -> True, "foo-asdf", "foo" -> False"""
        sourcebuildflavor = source.split(":")[1] if ":" in source else None
        releasebuildflavor = release.split(":")[1] if ":" in release else None
        return sourcebuildflavor == releasebuildflavor and release.startswith(source.split(":")[0] + ".")

    def _getNewestReleaseUrl(self):
        if self.newest_release_url is None:
            buildcontainername = self.url.split("/")[-1]
            prjurl = self.url + "/.."
            buildcontainerlist_req = requests.get(prjurl)
            buildcontainerlist = xml.fromstring(buildcontainerlist_req.content)
            releases = [entry for entry in buildcontainerlist.xpath("entry/@name") if
                        self._isMaintenanceReleaseOf(entry, buildcontainername)]
            releases.sort()
            # Pick the first one with binaries
            for release in releases[::-1] + [buildcontainername]:
                self.newest_release_url = prjurl + "/" + release
                try:
                    self._getFilename()
                    break
                except DockerFetchException:
                    continue

        return self.newest_release_url

    def _getFilename(self):
        """Return the name of the binary at the URL with the filename ending in
        .docker.tar."""
        binarylist_req = requests.get(self._getNewestReleaseUrl())
        binarylist = xml.fromstring(binarylist_req.content)
        for binary in binarylist.xpath("binary/@filename"):
            if binary.endswith(".docker.tar"):
                return binary

        raise DockerFetchException("No docker image built in the repository")

    def currentVersion(self):
        """Return {version}-?({flavor}-)Build{build} of the docker file."""
        filename = self._getFilename()
        # Capture everything between arch and filename suffix
        return re.match(r'[^.]*\.[^.]+-(.*)\.docker\.tar$', filename).group(1)

    def getDockerImage(self, callback):
        """Download the tar and extract it"""
        filename = self._getFilename()
        with tempfile.NamedTemporaryFile() as tar_file:
            tar_file.write(requests.get(self.newest_release_url + "/" + filename).content)
            with tempfile.TemporaryDirectory() as tar_dir:
                # Extract the .tar into the dir
                subprocess.call(f"tar -xaf '{tar_file.name}' -C '{tar_dir}'", shell=True)
                return callback(tar_dir)


def run():
    drc_tw = docker_registry.DockerRegistryClient(os.environ['REGISTRY'], os.environ['REGISTRY_USER'], os.environ['REGISTRY_PASSWORD'],
                                                  os.environ['REGISTRY_REPO_TW'])
    drc_leap = docker_registry.DockerRegistryClient(os.environ['REGISTRY'], os.environ['REGISTRY_USER'], os.environ['REGISTRY_PASSWORD'],
                                                    os.environ['REGISTRY_REPO_LEAP'])

    config = {
        'tumbleweed': {
            'fetchers': {
                'i586': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/i586/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
                'x86_64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/x86_64/opensuse-tumbleweed-image:docker", maintenance_release=True),   # noqa: E501
                'aarch64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/aarch64/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
                'armv7l': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/armv7l/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
                'armv6l': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/armv6l/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
                'ppc64le': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/ppc64le/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
                's390x': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/s390x/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
                'riscv64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Tumbleweed/containers/riscv64/opensuse-tumbleweed-image:docker", maintenance_release=True),  # noqa: E501
            },
            'publisher': DockerImagePublisherRegistry(drc_tw, "latest"),
        },
        'leap-15.5': {
            'fetchers': {
                'x86_64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.5/containers/x86_64/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'aarch64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.5/containers/aarch64/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'armv7l': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.5/containers_armv7/armv7l/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'ppc64le': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.5/containers/ppc64le/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                's390x': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.5/containers/s390x/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
            },
            'publisher': DockerImagePublisherRegistry(drc_leap, "15.5"),
        },
        'leap-15.6': {
            'fetchers': {
                'x86_64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/x86_64/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'aarch64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/aarch64/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'armv7l': None,
                'ppc64le': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/ppc64le/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                's390x': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/s390x/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
            },
            'publisher': DockerImagePublisherRegistry(drc_leap, "15.6"),
        },
        # Like Leap 15.6, but using the 15.5 image for armv7l
        'leap-15': {
            'fetchers': {
                'x86_64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/x86_64/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'aarch64': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/aarch64/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'armv7l': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.5/containers_armv7/armv7l/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                'ppc64le': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/ppc64le/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
                's390x': DockerImageFetcherOBS(url="https://build.opensuse.org/public/build/openSUSE:Containers:Leap:15.6/containers/s390x/opensuse-leap-image:docker", maintenance_release=True),  # noqa: E501
            },
            'publisher': DockerImagePublisherRegistry(drc_leap, "latest", ["15"]),
        },
    }

    # Parse args after defining the config - the available distros are included
    # in the help output
    parser = argparse.ArgumentParser(description="Docker image publish script",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("distros", metavar="distro", type=str, nargs="*",
                        default=[key for key in config],
                        help="Which distros to check for images to publish.")

    args = parser.parse_args()

    success = True

    for distro in args.distros:
        print(f"Handling {distro}")

        archs_to_update = {}
        fetchers = config[distro]['fetchers']
        publisher = config[distro]['publisher']

        for arch in fetchers:
            print(f"\tArchitecture {arch}")
            try:
                current = fetchers[arch].currentVersion() if fetchers[arch] else None
                print(f"\t\tAvailable version: {current}")

                released = publisher.releasedDockerImageVersion(arch)
                print(f"\t\tReleased version: {released}")

                if current != released:
                    archs_to_update[arch] = current
            except Exception as e:
                print(f"\t\tException during version fetching: {e}")

        if not archs_to_update:
            print("\tNothing to do.")
            continue

        if not publisher.prepareReleasing():
            print("\tCould not prepare the publishing")
            success = False
            continue

        need_to_upload = False

        for arch, version in archs_to_update.items():
            if fetchers[arch] is None:
                print(f"\tRemoving {arch} image")
                publisher.removeImage(arch)
                need_to_upload = True
                continue

            print(f"\tUpdating {arch} image to version {version}")
            try:
                fetchers[arch].getDockerImage(lambda image_path: publisher.addImage(version=version,
                                                                                    arch=arch,
                                                                                    image_path=image_path))
                need_to_upload = True

            except DockerFetchException as dfe:
                print(f"\t\tCould not fetch the image: {dfe}")
                success = False
                continue
            except DockerPublishException as dpe:
                print(f"\t\tCould not publish the image: {dpe}")
                success = False
                continue

        # If nothing got added to the publisher, don't try to upload it.
        # For docker hub it'll just update the "last pushed" time without any change
        if not need_to_upload:
            continue

        if not publisher.finishReleasing():
            print("\tCould not publish the image")
            continue

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(run())
