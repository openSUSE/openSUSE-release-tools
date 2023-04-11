#!/usr/bin/python3
import gzip
import sys
from collections import defaultdict
from lxml import etree

if len(sys.argv) != 2:
    print("Script to generate necessary FileProvides lines needed by OBS from repo data.", file=sys.stderr)
    print("Usage: repo2fileprovides.py primary.xml(.gz)", file=sys.stderr)
    sys.exit(1)

repofilename = sys.argv[1]
xmlfile = open(repofilename, 'rb')
if repofilename.endswith('.gz'):
    xmlfile = gzip.GzipFile(fileobj=xmlfile)

NS = {'md': 'http://linux.duke.edu/metadata/common',
      'rpm': 'http://linux.duke.edu/metadata/rpm'}
repodata = etree.parse(xmlfile)

# Step 1: Collect all provided files
# Set of all provided files
providedfiles = set()
# Map of filename -> set of packages providing it
fileprovides = defaultdict(set)

for pkg in repodata.iterfind('/md:package', namespaces=NS):
    pkgname = pkg.xpath('./md:name/text()', namespaces=NS)[0]
    # Implicit file provides
    for f in pkg.iterfind('./md:format/md:file', namespaces=NS):
        filename = f.text
        fileprovides[filename].add(pkgname)
        providedfiles.add(filename)

    # Explicit file provides
    for filename in pkg.xpath("./md:format/rpm:provides/rpm:entry[starts-with(@name, '/')]/@name",
                              namespaces=NS):
        fileprovides[filename].add(pkgname)
        providedfiles.add(filename)

# Step 2: Collect all required files
requiredfiles = set(repodata.xpath("/md:metadata/md:package/md:format/rpm:requires/rpm:entry[starts-with(@name, '/')]/@name",
                                   namespaces=NS))

# Split up boolean deps
booleandeps = set(repodata.xpath("/md:metadata/md:package/md:format/rpm:requires/rpm:entry"
                                 "[starts-with(@name, '(') and contains(@name, '/')]/@name",
                                 namespaces=NS))
for dep in booleandeps:
    for capability in dep.replace('(', ' ').replace(')', ' ').split():
        if capability[0] == '/':
            requiredfiles.add(capability)

# Step 3: For all provided files which are also required, print "FileProvides"
# lines
for filename in sorted(providedfiles.intersection(requiredfiles)):
    print(f"FileProvides: {filename} {' '.join(sorted(fileprovides[filename]))}")
