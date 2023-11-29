import fcntl
import itertools
import logging
import os
import osc.conf
import re
import struct
import sys
import tempfile

from lxml import etree as ET
from osc.core import http_GET
from osc.core import makeurl
from osc.util.cpio import CpioHdr
from urllib.parse import quote_plus

logger = logging.getLogger('RepoMirror')


class RepoMirror:
    def __init__(self, apiurl, nameignore: str = '-debug(info|source|info-32bit).rpm$'):
        """
        Class to mirror RPM headers of all binaries in a repo on OBS (full tree).
        Debug packages are ignored by default, see the nameignore parameter.
        """
        self.apiurl = apiurl
        self.nameignorere = re.compile(nameignore)

    def extract_cpio_stream(self, destdir: str, stream):
        hdr_fmt = '6s8s8s8s8s8s8s8s8s8s8s8s8s8s'
        hdr_len = 110

        name_re = re.compile('^([^/]+)-([0-9a-f]{32})$')

        while True:
            # Read and parse the CPIO header
            hdrdata = stream.read(hdr_len)
            hdrtuples = struct.unpack(hdr_fmt, hdrdata)
            if hdrtuples[0] != b'070701':
                raise NotImplementedError(f'CPIO format {hdrtuples[0]} not implemented')

            # The new-ascii format has padding for 4 byte alignment
            def align():
                stream.read((4 - (stream.tell() % 4)) % 4)

            hdr = CpioHdr(*hdrtuples)
            hdr.filename = stream.read(hdr.namesize - 1).decode('ascii')
            stream.read(1)  # Skip terminator
            align()

            binarymatch = name_re.match(hdr.filename)
            if hdr.filename == '.errors':
                content = stream.read(hdr.filesize)
                raise RuntimeError('Download has errors: ' + content.decode('ascii'))
            elif binarymatch:
                name = binarymatch.group(1)
                md5 = binarymatch.group(2)
                destpath = os.path.join(destdir, f'{md5}-{name}.rpm')
                with tempfile.NamedTemporaryFile(mode='wb', dir=destdir) as tmpfile:
                    # Probably not big enough to need chunking
                    tmpfile.write(stream.read(hdr.filesize))
                    os.link(tmpfile.name, destpath)
                    # Would be nice to use O_TMPFILE + link here, but python passes
                    # O_EXCL which breaks that.
                    # os.link(f'/proc/self/fd/{tmpfile.fileno()}', destpath)

                align()
            elif hdr.filename == 'TRAILER!!!':
                if stream.read(1):
                    raise RuntimeError('Expected end of CPIO')
                break
            else:
                raise NotImplementedError(f'Unhandled file {hdr.filename} in archive')

    def _mirror(self, destdir: str, prj: str, repo: str, arch: str) -> None:
        "Using the _repositories endpoint, download all RPM headers into destdir."
        logger.info(f'Mirroring {prj}/{repo}/{arch}')
        pkglistxml = http_GET(makeurl(self.apiurl, ['build', prj, repo, arch, '_repository'],
                                      query={'view': 'binaryversions', 'nometa': 1}))
        root = ET.parse(pkglistxml).getroot()
        remotebins: dict[str, str] = {}
        for binary in root.findall('binary'):
            name = binary.get('name')
            if name.endswith('.rpm') and not self.nameignorere.search(name):
                hdrmd5 = binary.get('hdrmd5')
                remotebins[f'{hdrmd5}-{name}'] = name[:-4]

        to_delete: list[str] = []
        for filename in os.listdir(destdir):
            if not filename.endswith('.rpm'):
                continue

            if filename in remotebins:
                del remotebins[filename]  # Already downloaded
            else:
                to_delete.append(os.path.join(destdir, filename))

        if to_delete:
            logger.info(f'Deleting {len(to_delete)} old packages')
            for path in to_delete:
                os.unlink(path)

        if remotebins:
            logger.info(f'Downloading {len(remotebins)} new packages')
            binaries = remotebins.values()

            # Download in batches of 50
            for chunk in range(0, len(binaries), 50):
                query = 'view=cpioheaders'
                for binary in itertools.islice(binaries, chunk, chunk + 50):
                    query += '&binary=' + quote_plus(binary)

                req = http_GET(makeurl(self.apiurl, ['build', prj, repo, arch, '_repository'],
                                       query=query))
                self.extract_cpio_stream(destdir, req)

    def mirror(self, destdir: str, prj: str, repo: str, arch: str) -> None:
        "Creates destdir and locks destdir/.lock before mirroring."
        os.makedirs(destdir, exist_ok=True)

        with open(os.path.join(destdir, '.lock'), 'w') as lockfile:
            try:
                fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except IOError:
                logger.info(destdir + 'is locked, waiting... ', end='')
                fcntl.flock(lockfile, fcntl.LOCK_EX)
                logger.info('acquired!')

            return self._mirror(destdir, prj, repo, arch)


if __name__ == '__main__':
    if len(sys.argv) != 6:
        print("Usage: repomirror.py apiurl destdir prj repo arch")
    else:
        osc.conf.get_config()
        rm = RepoMirror(sys.argv[1])
        rm.mirror(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
