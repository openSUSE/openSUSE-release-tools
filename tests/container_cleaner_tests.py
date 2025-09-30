import unittest

from container_cleaner import ContainerCleaner
from lxml import etree as xml


class MockedContainerCleaner(ContainerCleaner):
    def __init__(self, container_arch_map):
        self.container_arch_map = container_arch_map

    def getDirEntries(self, path):
        """Mock certain OBS APIs returning directory entries"""
        assert path == ["source", "mock:prj"]
        srccontainers = [a.split(":")[0] for a in self.container_arch_map.keys()]
        return list(set(srccontainers))  # Remove duplicates

    def getBinaryList(self, project):
        """Mock certain OBS APIs returning a list of binaries"""
        assert project == "mock:prj"

        resultlist = xml.fromstring('<resultlist state="6b99f3a517302521e047e4100dc32384"/>')
        all_archs = set()
        for archs in self.container_arch_map.values():
            all_archs |= set(archs)

        for arch in set(sum(self.container_arch_map.values(), [])):
            result = xml.fromstring('<result repository="containers" code="published" state="published"/>')
            result.set("project", project)
            result.set("arch", arch)

            for buildcontainer in self.container_arch_map:
                binarylist = xml.Element("binarylist", attrib={"package": buildcontainer})
                if arch in self.container_arch_map[buildcontainer]:
                    binarylist.append(xml.fromstring('<binary filename="A binary"/>'))

                result.append(binarylist)

            resultlist.append(result)

        return xml.ElementTree(element=resultlist)


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

        to_be_deleted_exp = ["c", "c.01", "c.02", "c.04",
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

        to_be_deleted_exp = ["c", "c.01", "c.02", "c.04",
                             "e.51"]

        return self.doTest(container_arch_map, to_be_deleted_exp)
