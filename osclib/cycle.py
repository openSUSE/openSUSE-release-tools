# Copyright (C) 2014 SUSE Linux Products GmbH
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

from copy import deepcopy
import urllib2
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
    """Class to detect cycles in Factory."""

    def __init__(self, apiurl):
        self.apiurl = apiurl
        # Store packages prevoiusly ignored. Don't pollute the screen.
        self._ignore_packages = set()

    @memoize(ttl=60*60*6)
    def _builddepinfo(self, project, repository, arch):
        root = None
        try:
            # print('Generating _builddepinfo for (%s, %s, %s)' % (project, repository, arch))
            url = makeurl(self.apiurl, ['/build/%s/%s/%s/_builddepinfo' % (project, repository, arch)])
            root = http_GET(url).read()
        except urllib2.HTTPError, e:
            print('ERROR in URL %s [%s]' % (url, e))
        return root

    def _get_builddepinfo(self, project, repository, arch, package):
        """Get the builddep info for a single package"""
        root = ET.fromstring(self._builddepinfo(project, repository, arch))
        packages = [Package(element=e) for e in root.findall('package')]
        package = [p for p in packages if p.pkg == package]
        return package[0] if package else None

    def _get_builddepinfo_graph(self, project, repository, arch):
        """Generate the buildepinfo graph for a given architecture."""

        _IGNORE_PREFIX = ('texlive-', 'master-boot-code')

        # Note, by default generate the graph for all Factory. If you only
        # need the base packages you can use:
        #   project = 'Base:System'
        #   repository = 'openSUSE_Factory'

        root = ET.fromstring(self._builddepinfo(project, repository, arch))
        # Reset the subpackages dict here, so for every graph is a
        # different object.
        packages = [Package(element=e) for e in root.findall('package')]

        # XXX - Ugly Exception. We need to ignore branding packages and
        # packages that one of his dependencies do not exist. Also ignore
        # preinstall images.
        packages = [p for p in packages if not ('branding' in p.pkg or p.pkg.startswith('preinstallimage-'))]

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
            deps = [d for d in p.deps if 'branding' not in d]
            missing = [d for d in deps if not d.startswith(_IGNORE_PREFIX) and d not in subpkgs]
            if missing:
                if p.pkg not in self._ignore_packages:
                    # print 'Ignoring package. Missing dependencies %s -> (%s) %s...' % (p.pkg, len(missing), missing[:5])
                    self._ignore_packages.add(p.pkg)
                continue

            # XXX - Ugly Hack. Subpagackes for texlive are not correctly
            # generated. If the dependency starts with texlive- prefix,
            # assume that the correct source package is texlive.
            graph.add_edges_from((p.pkg, subpkgs[d] if not d.startswith('texlive-') else 'texlive')
                                 for d in deps if not d.startswith('master-boot-code'))

        # Store the subpkgs dict in the graph. It will be used later.
        graph.subpkgs = subpkgs
        return graph

    def _get_builddepinfo_cycles(self, package, repository, arch):
        """Generate the buildepinfo cycle list for a given architecture."""
        root = ET.fromstring(self._builddepinfo(package, repository, arch))
        return frozenset(frozenset(e.text for e in cycle.findall('package'))
                         for cycle in root.findall('cycle'))

    def cycles(self, requests, project='openSUSE:Factory', repository='standard', arch='x86_64'):
        """Detect cycles in a specific repository."""

        # Detect cycles - We create the full graph from _builddepinfo.
        factory_graph = self._get_builddepinfo_graph(project, repository, arch)
        factory_cycles = factory_graph.cycles()

        # This graph will be updated for every request
        current_graph = deepcopy(factory_graph)

        subpkgs = current_graph.subpkgs

        # Recover all packages at once, ignoring some packages that
        # can't be found in x86_64 architecture.
        #
        # The first filter is to remove some packages that do not have
        # `goodrepos`. Those packages are usually marked as 'rq.update
        # = True' (meaning that they are declined or there is a new
        # updated review).
        # all_packages = [self._get_builddepinfo(rq.src_project, rq.goodrepos[0], arch, rq.src_package)
        #                 for rq in requests if not rq.updated]

        # 'goodrepo' is a tuple (project, repository, disturl).  We
        # take the ones that match the project from the request and
        # take the first repository.
        goodrepos = {
            rq: [repo for (project, repo) in rq.goodrepos if rq.src_project == project][0] for rq in requests
        }
        all_packages = [self._get_builddepinfo(rq.src_project, goodrepos[rq], arch, rq.src_package)
                        for rq in requests if not rq.updated]
        all_packages = [pkg for pkg in all_packages if pkg]

        subpkgs.update(dict((p, pkg.pkg) for pkg in all_packages for p in pkg.subs))

        for pkg in all_packages:
            # Update the current graph and see if we have different cycles
            edges_to = ()
            if pkg.pkg in current_graph:
                current_graph[pkg.pkg] = pkg
                current_graph.remove_edges_from(set((pkg.pkg, p) for p in current_graph.edges(pkg.pkg)))
                edges_to = current_graph.edges_to(pkg.pkg)
                current_graph.remove_edges_from(set((p, pkg.pkg) for p in edges_to))
            else:
                current_graph.add_node(pkg.pkg, pkg)
            current_graph.add_edges_from((pkg.pkg, subpkgs[p]) for p in pkg.deps if p in subpkgs)
            current_graph.add_edges_from((p, pkg.pkg) for p in edges_to
                                         if pkg.pkg in set(subpkgs[sp] for sp in current_graph[p].deps if sp in subpkgs))

        for cycle in current_graph.cycles():
            if cycle not in factory_cycles:
                factory_edges = set((u, v) for u in cycle for v in factory_graph.edges(u) if v in cycle)
                current_edges = set((u, v) for u in cycle for v in current_graph.edges(u) if v in cycle)
                yield cycle, sorted(current_edges - factory_edges)
