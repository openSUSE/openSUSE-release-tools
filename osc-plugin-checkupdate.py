# Copyright (c) 2012,2013 SUSE Linux Products GmbH
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

import osc
import osc.core
import urllib2

from osc import cmdln
from osc import conf

def _xxx_diff_filter_changes(self, diff):
    lines = []
    ischanges = False
    for line in diff.split('\n'):
        if not len(line):
            continue

        if line.startswith('--- '):
            fn = line[4:]
            if ' ' in fn:
                fn=fn[0:fn.index(' ')]
            if fn.endswith('.changes'):
                ischanges = True
            else:
                ischanges = False

        if ischanges:
            lines.append(line)

    return lines

@cmdln.option('--project', action='store', help='project')
@cmdln.alias('killupdate')
def do_checkupdate(self, subcmd, opts, *args):
    """
    compare packages in XX.X:Update with those in XX.X
      osc checkupdate
    delete an update
      osc killupdate INCIDENTNUMBER
    """
    CHECK = 1
    KILL = 2
    mode = CHECK

    if subcmd == 'checkupdate':
        mode = CHECK
    elif subcmd == 'killupdate':
        mode = KILL
        if len(args) > 0:
            num = int(args[0])
        else:
            raise oscerr.WrongArgs('specify incident number')
    else:
        raise oscerr.WrongArgs('invalid command')


    if opts.project:
        dprj = opts.project
    else:
        dprj = "openSUSE:13.1"
    prj = dprj+":Update"

    msg = "cleanup for RC1"

    apiurl = self.get_api_url()
    pkgs = meta_get_packagelist(apiurl, prj)

    number_re = re.compile(r".*\.(\d+)$")

    if mode == CHECK:
        print "running diff %s %s"%(prj, dprj)

    todel = []
    for p in pkgs:
        if mode == KILL:
            sfx=".%d"%num
            if p.endswith(sfx):
                try:
                    url = makeurl(apiurl, ['build', prj, 'standard', 'i586', p])
                    f = http_GET(url)
                    root = ET.parse(f).getroot()
                    for node in root.findall('binary'):
                        fn = node.get('filename')
                        if not fn.endswith('.src.rpm'):
                            continue
                        print 'src:', fn[:-len('.src.rpm')]
                except urllib2.HTTPError, e:
                    if e.code != 404:
                        print "error:", e
                        continue
                    print "no binaries found"
                print "delete %s"%p
                delete_package(apiurl, prj, p, False, msg)
                if not p.startswith('patchinfo.'):
                    pn = p[:-len(sfx)]
                    print "delete %s"%pn
                    try:
                        True
                        delete_package(apiurl, prj, pn, False, msg)
                    except urllib2.HTTPError, e:
                        if e.code == 404:
                            print "not found, skip"
                            continue
                        raise e
        elif mode == CHECK:
            islink = False
            locallink = False
            linktomain = False
            incidentnr = None

            # XXX: should check type instead
            if p.startswith("patchinfo."):
                continue

            print "checking %s" % p

            url = makeurl(apiurl, ['source', prj, p, '_link'])
            try:
                f = http_GET(url)
                islink = True
                root = ET.parse(f).getroot()
                linkprj = root.get('project')
                linkpkg = root.get('package')
                if not linkprj or linkprj == prj:
                    locallink = True
                    # ignore links to main package
                    m1 = number_re.match(p)
                    m2 = number_re.match(linkpkg)
                    if m1 and m2 and m1.groups()[0] == m2.groups()[0]:
                        linktomain = True
                    incidentnr = m2.groups()[0]
            except urllib2.HTTPError, e:
                if e.code != 404:
                    print "error:", e
                    continue
                else:
                    # ignore not linked packages for now
                    islink = False

            if not islink or linktomain:
                continue

            try:
                diff = server_diff(apiurl, prj, p, None, dprj, p, None)
            except urllib2.HTTPError, e:
                print "error:", e
                continue

            if diff:
                added = 0
                removed = 0
                ischanges = False
                for line in diff.split('\n'):
                    if not len(line):
                        continue

                    if line.startswith('--- '):
                        fn = line[4:]
                        if ' ' in fn:
                            fn=fn[0:fn.index(' ')]
                        if fn.endswith('.changes'):
                            ischanges = True
                        else:
                            ischanges = False

                    if line.startswith('--- ') or line.startswith('+++ '):
                        continue

                    if ischanges:
                        if line[0] == '+':
                            added += 1
                        elif line[0] == '-':
                            removed += 1

                print "+++ %s: %s has changes (%d+, %d-)" % (incidentnr, p, added, removed)
            else:
                print "%s: %s ok" % (incidentnr, p)

# vim: sw=4 et
