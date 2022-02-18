import unittest

from container_cleaner import ContainerCleaner

class MockedContainerCleaner(ContainerCleaner):
    def __init__(self, container_arch_map):
        self.container_arch_map = container_arch_map

    def getDirEntries(self, path):
        """Mock certain OBS APIs returning directory entries"""
        if path == ["source", "mock:prj"]:
            srccontainers = [a.split(":")[0] for a in self.container_arch_map.keys()]
            return list(set(srccontainers))  # Remove duplicates
        elif path == ["build", "mock:prj", "containers"]:
            all_archs = []
            for archs in self.container_arch_map.values():
                all_archs += archs

            return list(set(all_archs))
        elif path[0:3] == ["build", "mock:prj", "containers"] and len(path) == 4:
            arch = path[3]
            ret = []
            for srccontainer in self.container_arch_map:
                ret += [srccontainer]

            return ret
        else:
            raise RuntimeError("Path %s not expected" % path)

    def getDirBinaries(self, path):
        """Mock certain OBS APIs returning a list of binaries"""
        if path[0:3] == ["build", "mock:prj", "containers"] and len(path) == 5:
            arch = path[3]
            srccontainer = path[4]
            if arch in self.container_arch_map[srccontainer]:
                return ["A binary"]

            return []
        else:
            raise RuntimeError("Path %s not expected" % path)


class TestContainerCleaner(unittest.TestCase):
    def doTest(self, container_arch_map, to_be_deleted_exp):
        cleaner = MockedContainerCleaner(container_arch_map)
        to_be_deleted = cleaner.findSourcepkgsToDelete("mock:prj")
        to_be_deleted.sort()
        self.assertEqual(to_be_deleted, to_be_deleted_exp)

    def test_empty(self):
        """Empty project, do nothing"""
        container_arch_map = {}

        to_be_deleted_exp = []

        return self.doTest(container_arch_map, to_be_deleted_exp)

    def test_nothingToDo(self):
        """Non-empty project, still do nothing"""
        container_arch_map = {"c": ["i586", "x86_64"],
                        "c.01": ["i586"],
                        "c.02": ["x86_64"],
                        "c.04": ["i586", "x86_64"],
                        "c.06": ["i586"],
                        "c.07": ["x86_64"],
                        "c.08": ["i586", "x86_64"],
                        "c.11": [],
                        "d.42": [], "d.43": []}

        to_be_deleted_exp = []

        return self.doTest(container_arch_map, to_be_deleted_exp)

    def test_multiplePackages(self):
        """Multiple packages in one project"""
        container_arch_map = {"c": ["i586", "x86_64"],
                        "c.01": ["i586"],
                        "c.02": ["x86_64"],
                        "c.03": [],
                        "c.04": ["i586", "x86_64"],
                        "c.05": ["i586", "x86_64"],
                        "c.06": ["i586"],
                        "c.07": ["x86_64"],
                        "c.08": ["i586", "x86_64"],
                        "c.09": ["i586", "x86_64"],
                        "c.10": ["i586", "x86_64"],
                        "c.11": [],
                        "d.42": [], "d.43": [],
                        "e.51": ["i586"],
                        "e.52": ["aarch64"],
                        "e.53": ["i586"],
                        "e.54": ["i586"],
                        "e.55": ["i586"],
                        "e.56": ["i586"],
                        "e.57": ["i586"]}

        to_be_deleted_exp = ["c", "c.01", "c.02", "c.03", "c.04",
                             "e.51"]

        return self.doTest(container_arch_map, to_be_deleted_exp)

    def test_multibuild(self):
        """Packages using _multbuild.
        There is no special handling for _multibuild - It's enough if any flavor has binaries."""
        container_arch_map = {"c:docker": ["i586", "x86_64"],
                        "c.01:docker": ["i586"],
                        "c.02:lxc": ["x86_64"],
                        "c.03:docker": [],
                        "c.04": ["i586", "x86_64"],
                        "c.05:docker": ["i586", "x86_64"],
                        "c.06:docker": ["i586"],
                        "c.07:docker": ["x86_64"],
                        "c.08:docker": ["i586", "x86_64"],
                        "c.09:docker": ["i586", "x86_64"],
                        "c.10:docker": ["i586", "x86_64"],
                        "c.11:docker": [],
                        "d.42:lxc": [], "d.43": [],
                        "e.51": ["i586"],
                        "e.52": ["aarch64"],
                        "e.53": ["i586"],
                        "e.54:docker": ["i586"],
                        "e.55:docker": ["i586"],
                        "e.56": ["i586"],
                        "e.57": ["i586"]}

        to_be_deleted_exp = ["c", "c.01", "c.02", "c.03", "c.04",
                             "e.51"]

        return self.doTest(container_arch_map, to_be_deleted_exp)
