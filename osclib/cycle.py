try:
    from urllib.error import HTTPError
except ImportError:
    # python 2.x
    from urllib2 import HTTPError

from xml.etree import cElementTree as ET

from osc.core import http_GET
from osc.core import makeurl

from .memoize import memoize


class Graph(dict):
    """Graph object. Inspired in NetworkX data model."""

    def __init__(self):
        """Initialize an empty graph."""
        #  The nodes are stored in the Graph dict itself, but the
        #  adjacent list is stored as an attribute.
        self.adj = {}

    def add_node(self, name, value):
        """Add a node in the graph."""
        self[name] = value
        if name not in self.adj:
            self.adj[name] = set()

    def add_nodes_from(self, nodes_and_values):
        """Add multiple nodes"""
        for node, value in nodes_and_values:
            self.add_node(node, value)

    def add_edge(self, u, v, directed=True):
        """Add the edge u -> v, an v -> u if not directed."""
        self.adj[u].add(v)
        if not directed:
            self.adj[v].add(u)

    def add_edges_from(self, edges, directed=True):
        """Add the edges from an iterator."""
        for u, v in edges:
            self.add_edge(u, v, directed)

    def remove_edge(self, u, v, directed=True):
        """Remove the edge u -> v, an v -> u if not directed."""
        try:
            self.adj[u].remove(v)
        except KeyError:
            pass
        if not directed:
            try:
                self.adj[v].remove(u)
            except KeyError:
                pass

    def remove_edges_from(self, edges, directed=True):
        """Remove the edges from an iterator."""
        for u, v in edges:
            self.remove_edge(u, v, directed)

    def edges(self, v):
        """Get the adjancent list for a vertex."""
        return sorted(self.adj[v]) if v in self else ()

    def edges_to(self, v):
        """Get the all the vertex that point to v."""
        return sorted(u for u in self.adj if v in self.adj[u])

    def cycles(self):
        """Detect cycles using Tarjan algorithm."""
        index = [0]
        path = []
        cycles = []

        v_index = {}
        v_lowlink = {}

        def scc(node, v):
            v_index[v], v_lowlink[v] = index[0], index[0]
            index[0] += 1
            path.append(node)

            for succ in self.adj.get(node, []):
                w = self[succ]
                if w not in v_index:
                    scc(succ, w)
                    v_lowlink[v] = min(v_lowlink[v], v_lowlink[w])
                elif succ in path:
                    v_lowlink[v] = min(v_lowlink[v], v_index[w])

            if v_index[v] == v_lowlink[v]:
                i = path.index(node)
                path[:], cycle = path[:i], frozenset(path[i:])
                if len(cycle) > 1:
                    cycles.append(cycle)

        for node in sorted(self):
            v = self[node]
            if not getattr(v, 'index', 0):
                scc(node, v)
        return frozenset(cycles)


class Package(object):
    """Simple package container. Used in a graph as a vertex."""

    def __init__(self, pkg=None, src=None, deps=None, subs=None,
                 element=None):
        self.pkg = pkg
        self.src = src
        self.deps = deps
        self.subs = subs
        if element:
            self.load(element)

    def load(self, element):
        """Load a node from a ElementTree package XML element"""
        self.pkg = element.attrib['name']
        self.src = [e.text for e in element.findall('source')]
        assert len(self.src) == 1, 'There are more that one source packages in the graph'
        self.src = self.src[0]
        self.deps = set(e.text for e in element.findall('pkgdep'))
        self.subs = set(e.text for e in element.findall('subpkg'))

    def __repr__(self):
        return 'PKG: %s\nSRC: %s\nDEPS: %s\n SUBS: %s' % (self.pkg,
                                                          self.src,
                                                          self.deps,
                                                          self.subs)


class CycleDetector(object):
    """Class to detect cycles in an OBS project."""

    def __init__(self, apiurl):
        self.apiurl = apiurl
        # Store packages prevoiusly ignored. Don't pollute the screen.
        self._ignore_packages = set()

    def _builddepinfo(self, project, repository, arch):
        root = None
        try:
            # print('Generating _builddepinfo for (%s, %s, %s)' % (project, repository, arch))
            url = makeurl(self.apiurl, ['build/%s/%s/%s/_builddepinfo' % (project, repository, arch)])
            root = http_GET(url).read()
        except HTTPError as e:
            print('ERROR in URL %s [%s]' % (url, e))
        return root

    def _get_builddepinfo_graph(self, project, repository, arch):
        """Generate the buildepinfo graph for a given architecture."""

        # Note, by default generate the graph for all Factory /
        # 13/2. If you only need the base packages you can use:
        #   project = 'Base:System'
        #   repository = 'openSUSE_Factory'

        root = ET.fromstring(self._builddepinfo(project, repository, arch))
        # Reset the subpackages dict here, so for every graph is a
        # different object.
        packages = [Package(element=e) for e in root.findall('package')]

        graph = Graph()
        graph.add_nodes_from((p.pkg, p) for p in packages)

        subpkgs = {}    # Given a subpackage, recover the source package
        for p in packages:
            # Check for packages that provides the same subpackage
            for subpkg in p.subs:
                if subpkg in subpkgs:
                    # print 'Subpackage duplication %s - %s (subpkg: %s)' % (p.pkg, subpkgs[subpkg], subpkg)
                    pass
                else:
                    subpkgs[subpkg] = p.pkg

        for p in packages:
            # Calculate the missing deps
            deps = p.deps
            missing = set(deps) - set(subpkgs)
            if missing:
                if p.pkg not in self._ignore_packages:
                    # print 'Ignoring package. Missing dependencies %s -> (%s) %s...' % (p.pkg, len(missing), missing[:5])
                    self._ignore_packages.add(p.pkg)
                continue

            graph.add_edges_from((p.pkg, subpkgs[d]) for d in deps)

        # Store the subpkgs dict in the graph. It will be used later.
        graph.subpkgs = subpkgs
        return graph

    def cycles(self, override_pair, overridden_pair, arch):
        """Detect cycles in a specific repository."""

        # Detect cycles - We create the full graph from _builddepinfo.
        project_graph = self._get_builddepinfo_graph(overridden_pair[0], overridden_pair[1], arch)
        current_graph = self._get_builddepinfo_graph(override_pair[0], override_pair[1], arch)

        # Sometimes, new cycles have only new edges, but not new
        # packages.  We need to inform about this, so this can become
        # a warning instead of an error.
        #
        # To do that, we store in `project_cycles_pkgs` all the
        # project (i.e Factory) cycles as a set of packages, so we can
        # check if the new cycle (also as a set of packages) is
        # included here.
        project_cycles = project_graph.cycles()
        project_cycles_pkgs = [set(cycle) for cycle in project_cycles]
        for cycle in current_graph.cycles():
            if cycle not in project_cycles:
                project_edges = set((u, v) for u in cycle for v in project_graph.edges(u) if v in cycle)
                current_edges = set((u, v) for u in cycle for v in current_graph.edges(u) if v in cycle)
                current_pkgs = set(cycle)
                yield (cycle,
                       sorted(current_edges - project_edges),
                       not any(current_pkgs.issubset(cpkgs) for cpkgs in project_cycles_pkgs))
