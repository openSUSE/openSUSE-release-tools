#!/usr/bin/python
# Copyright (c) 2014 SUSE Linux Products GmbH
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

import struct

class Cpio(object):
    def __init__(self, buf):
        self.buf = buf
        self.off = 0

    def __iter__(self):
        return self

    def next(self):
        f = CpioFile(self.off, self.buf)
        if f.fin():
            raise StopIteration
        self.off = self.off+f.length()
        return f

class CpioFile(object):
    def __init__(self, off, buf):
        self.off = off
        self.buf = buf
        
        if off&3:
            raise Exception("invalid offset %d"% off)

        fmt = "6s8s8s8s8s8s8s8s8s8s8s8s8s8s"
        off = self.off + struct.calcsize(fmt)

        fields = struct.unpack(fmt, buf[self.off:off])

	if fields[0] != "070701":
		raise Exception("invalid cpio header %s"%self.c_magic)

        names = ("c_ino", "c_mode", "c_uid", "c_gid",
                "c_nlink", "c_mtime", "c_filesize",
                "c_devmajor", "c_devminor", "c_rdevmajor",
                "c_rdevminor", "c_namesize", "c_check")
        for (n, v) in zip(names, fields[1:]):
            setattr(self, n, int(v, 16))

        nlen = self.c_namesize - 1
        self.name = struct.unpack('%ds'%nlen, buf[off:off+nlen])[0]
        off = off + nlen + 1
        if off&3:
            off = off + 4-(off&3) # padding
        self.payloadstart = off

    def fin(self):
        return self.name == 'TRAILER!!!'

    def __str__(self):
        return "[%s %d]"%(self.name, self.c_filesize)

    def header(self):
        return self.buf[self.payloadstart:self.payloadstart+self.c_filesize]

    def length(self):
        l = self.payloadstart-self.off + self.c_filesize
        if self.c_filesize&3:
            l = l + 4-(self.c_filesize&3)
        return l

if __name__ == '__main__':
    from optparse import OptionParser

    parser = OptionParser()
    parser.add_option("--debug", action="store_true", help="debug output")
    parser.add_option("--verbose", action="store_true", help="verbose")

    (options, args) = parser.parse_args()

    for fn in args:
        fh = open(fn, 'rb')
        cpio = Cpio(fh.read())
        for i in cpio:
            print i
            ofh = open(i.name, 'wb')
            ofh.write(i.header())
            ofh.close()

# vim: sw=4 et
