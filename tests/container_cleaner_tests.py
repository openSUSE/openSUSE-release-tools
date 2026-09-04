import unittest

from container_cleaner import ContainerCleaner
from lxml import etree as xml


class MockedContainerCleaner(ContainerCleaner):
    def __init__(self, container_arch_map):
        super().__init__()
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
        to_be_deleted = list(cleaner.findSourcepkgsToDelete("mock:prj"))
        to_be_deleted.sort()
        self.assertEqual(to_be_deleted, to_be_deleted_exp)

    def test_empty(self):
        """Empty project, do nothing"""
        container_arch_map = {}

        to_be_deleted_exp = []

        return self.doTest(container_arch_map, to_be_deleted_exp)

    def test_nothingToDo(self):
        """Non-empty project, still do nothing"""
        container_arch_map = {"c.00": ["i586", "x86_64"],
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
        container_arch_map = {"c.00": ["i586", "x86_64"],
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

        to_be_deleted_exp = ["c.00", "c.01", "c.02", "c.04",
                             "e.51"]

        return self.doTest(container_arch_map, to_be_deleted_exp)

    def test_multibuild(self):
        """_multibuild flavors are treated like separate binaries"""
        container_arch_map = {"c.00:docker": ["i586", "x86_64"],
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
                              "d.42:lxc": [],
                              "d.43": [],
                              "e.51": ["i586"],
                              "e.52": ["aarch64"],
                              "e.53": ["i586"],
                              "e.54:docker": ["i586"],
                              "e.55:docker": ["i586"],
                              "e.56": ["i586"],
                              "e.57": ["i586"]}

        to_be_deleted_exp = ["c.00", "c.01"]

        return self.doTest(container_arch_map, to_be_deleted_exp)

    def test_multibuild_independent(self):
        """_multibuild flavors are treated like separate binaries"""
        container_arch_map = {"f.0:a": ["x86_64"],
                              "f.0:b": ["x86_64"],
                              "f.1:b": ["x86_64"],
                              "f.2:b": ["x86_64"],
                              "f.3:b": ["x86_64"],
                              "f.4:b": ["x86_64"],
                              "f.5:b": ["x86_64"],
                              "f.6:b": ["x86_64"]}

        # f.0 is needed for f.0:a, keep it
        to_be_deleted_exp = ["f.1"]

        self.doTest(container_arch_map, to_be_deleted_exp)

    def test_multibuild_manyflavors(self):
        """Packages using _multbuild with many flavors
        Ensure that every flavors is accounted for separately."""
        container_arch_map = {}

        # Generate five releases of a container with six flavors each.
        # The flavors are released separately, so each flavor gets a new maintenance release
        # number, i.e. c.00:fl0 and c.01:fl1 have binaries,
        # while c.00:fl1-fl6, c.01:fl0 and c.01:fl2-6 do not.
        relcounter = 0
        for release in range(0, 4):
            for flavor in range(0, 6):
                relcounter = 6 * release + flavor
                for allflavor in range(0, 6):
                    container_arch_map[f"c.{relcounter}:fl{allflavor}"] = []

                container_arch_map[f"c.{relcounter}:fl{flavor}"] = ["x86_64"]

        # For each c:flX release, there are five binaries, thus nothing should get deleted.
        to_be_deleted_exp = []

        self.doTest(container_arch_map, to_be_deleted_exp)
