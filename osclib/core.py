import osc.core
from osc.core import makeurl
from osc.core import http_GET

from osclib.memoize import memoize

from xml.etree import cElementTree as ET


@memoize(session=True)
def owner_fallback(apiurl, project, package):
    root = osc.core.owner(apiurl, package, project=project)
    owner = root.find('owner')
    if not owner or owner.get('project') == project:
        # Fallback to global (ex Factory) maintainer.
        root = osc.core.owner(apiurl, package)
    return root

def maintainers_get(apiurl, project, package):
    """Get the maintainer of the package involved in the package."""
    root = owner_fallback(apiurl, project, package)
    maintainers = [p.get('name') for p in root.findall('.//person') if p.get('role') == 'maintainer']
    if not maintainers:
        for group in [p.get('name') for p in root.findall('.//group') if p.get('role') == 'maintainer']:
            url = makeurl(apiurl, ('group', group))
            root = ET.parse(http_GET(url)).getroot()
            maintainers = maintainers + [p.get('userid') for p in root.findall('./person/person')]
    return maintainers
