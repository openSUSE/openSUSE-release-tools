from xml.etree import cElementTree as ET

from osc.core import http_GET
from osc.core import makeurl

from osclib.memoize import memoize

@memoize(session=True)
def package_list(apiurl, project):
    url = makeurl(apiurl, ['source', project], { 'expand': 1 })
    root = ET.parse(http_GET(url)).getroot()

    packages = []
    for package in root.findall('entry'):
        packages.append(package.get('name'))

    return sorted(packages)
