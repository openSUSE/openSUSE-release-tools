import unittest

from container_cleaner import ContainerCleaner

class MockedContainerCleaner(ContainerCleaner):
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
                          "c.11": []}

    def getDirEntries(self, path):
        if path == ["source", "mock:prj"]:
            return self.container_arch_map.keys()
        elif path == ["build", "mock:prj", "containers"]:
            return ["i586", "x86_64"]
        elif path[0:3] == ["build", "mock:prj", "containers"] and len(path) == 4:
            arch = path[3]
            ret = []
            for srccontainer in self.container_arch_map:
                if arch in self.container_arch_map[srccontainer]:
                    ret += [srccontainer]
            return ret
        else:
            raise RuntimeError("Path %s not expected" % path)

    def getDirBinaries(self, path):
        if path[0:3] == ["build", "mock:prj", "containers"] and len(path) == 5:
            arch = path[3]
            srccontainer = path[4]
            if arch in self.container_arch_map[srccontainer]:
                return ["A binary"]

            return []
        else:
            raise RuntimeError("Path %s not expected" % path)


class TestContainerCleaner(unittest.TestCase):
    def setUp(self):
        self.victim = MockedContainerCleaner()

    def test_all(self):
        to_be_deleted = self.victim.findSourcepkgsToDelete("mock:prj")
        to_be_deleted.sort()
        self.assertEqual(to_be_deleted,
                         ["c", "c.01", "c.02", "c.03", "c.04"])
