import gzip
import importlib.util
import io
import os
import pickle
import shutil
import sys
import tempfile
import unittest
import zstandard

# Ensure parent directory is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'factory-package-news')))

spec = importlib.util.spec_from_file_location(
    "factory_package_news",
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'factory-package-news', 'factory-package-news.py'))
)
fpn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fpn)


class TestFactoryPackageNews(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_normalize_srpm(self):
        # Kernel package should be rewritten to kernel-source
        srpm = "kernel-default-6.14.0-1.1.src.rpm"
        self.assertEqual(fpn.normalize_srpm(srpm), "kernel-source-6.14.0-1.1.src.rpm")

        srpm_debug = "kernel-debug-6.14.0-1.1.src.rpm"
        self.assertEqual(fpn.normalize_srpm(srpm_debug), "kernel-source-6.14.0-1.1.src.rpm")

        # texlive-specs packages should be normalized to texlive-specs-<version>-1.src.rpm
        srpm_tex_a = "texlive-specs-a-2026-61.3.src.rpm"
        self.assertEqual(fpn.normalize_srpm(srpm_tex_a), "texlive-specs-2026-1.src.rpm")

        srpm_tex_z = "texlive-specs-z-2026-59.4.src.rpm"
        self.assertEqual(fpn.normalize_srpm(srpm_tex_z), "texlive-specs-2026-1.src.rpm")

        # Non-kernel / non-texlive package should stay unchanged
        srpm_normal = "bash-5.2.26-1.1.src.rpm"
        self.assertEqual(fpn.normalize_srpm(srpm_normal), "bash-5.2.26-1.1.src.rpm")

    def test_open_compressed_plain_and_gzip(self):
        # Plain text
        plain_path = os.path.join(self.test_dir, "test.txt")
        with open(plain_path, "wb") as f:
            f.write(b"plain content")
        with fpn.open_compressed(plain_path) as stream:
            self.assertEqual(stream.read(), b"plain content")

        # Gzip compressed
        gz_path = os.path.join(self.test_dir, "test.txt.gz")
        with gzip.open(gz_path, "wb") as f:
            f.write(b"gzip content")
        with fpn.open_compressed(gz_path) as stream:
            self.assertEqual(stream.read(), b"gzip content")

    def test_open_compressed_zstd(self):
        zst_path = os.path.join(self.test_dir, "test.txt.zst")
        cctx = zstandard.ZstdCompressor()
        with open(zst_path, "wb") as f:
            f.write(cctx.compress(b"zstd content"))

        with fpn.open_compressed(zst_path) as stream:
            self.assertEqual(stream.read(), b"zstd content")

    def test_find_repomd(self):
        # Directory without repomd
        self.assertIsNone(fpn.find_repomd(self.test_dir))

        # Direct repomd.xml file
        repodata_dir = os.path.join(self.test_dir, "repodata")
        os.makedirs(repodata_dir)
        repomd_path = os.path.join(repodata_dir, "repomd.xml")
        with open(repomd_path, "w") as f:
            f.write("<repomd/>")

        # 1. Pointing to repomd.xml directly
        found_source, base = fpn.find_repomd(repomd_path)
        self.assertEqual(found_source, repomd_path)
        self.assertEqual(base, self.test_dir)

        # 2. Pointing to repodata/ directory
        found_source, base = fpn.find_repomd(repodata_dir)
        self.assertEqual(found_source, repomd_path)
        self.assertEqual(base, self.test_dir)

        # 3. Pointing to repo root directory
        found_source, base = fpn.find_repomd(self.test_dir)
        self.assertEqual(found_source, repomd_path)
        self.assertEqual(base, os.path.abspath(self.test_dir))

    def _create_synthetic_repo(self, repo_dir, pkg_version="1.0", changelog_entries=None):
        repodata_dir = os.path.join(repo_dir, "repodata")
        os.makedirs(repodata_dir, exist_ok=True)

        if changelog_entries is None:
            changelog_entries = [
                (1700000000, "First change"),
                (1710000000, "Second change"),
            ]

        pri_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<metadata xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm" packages="5">
  <package type="rpm">
    <name>mypkg</name>
    <arch>x86_64</arch>
    <version epoch="0" ver="{pkg_version}" rel="1.1"/>
    <checksum type="sha256" pkgid="YES">pkgid_mypkg_{pkg_version}</checksum>
    <format>
      <rpm:sourcerpm>mysrc-{pkg_version}-1.1.src.rpm</rpm:sourcerpm>
    </format>
  </package>
  <package type="rpm">
    <name>mypkg-devel</name>
    <arch>x86_64</arch>
    <version epoch="0" ver="{pkg_version}" rel="1.1"/>
    <checksum type="sha256" pkgid="YES">pkgid_mypkg_devel_{pkg_version}</checksum>
    <format>
      <rpm:sourcerpm>mysrc-{pkg_version}-1.1.src.rpm</rpm:sourcerpm>
    </format>
  </package>
  <package type="rpm">
    <name>mypkg-doc</name>
    <arch>noarch</arch>
    <version epoch="0" ver="{pkg_version}" rel="1.1"/>
    <checksum type="sha256" pkgid="YES">pkgid_mypkg_doc_{pkg_version}</checksum>
    <format>
      <rpm:sourcerpm>mysrc-{pkg_version}-1.1.src.rpm</rpm:sourcerpm>
    </format>
  </package>
  <package type="rpm">
    <name>kernel-default</name>
    <arch>x86_64</arch>
    <version epoch="0" ver="6.14.0" rel="1.1"/>
    <checksum type="sha256" pkgid="YES">pkgid_kernel</checksum>
    <format>
      <rpm:sourcerpm>kernel-default-6.14.0-1.1.src.rpm</rpm:sourcerpm>
    </format>
  </package>
  <package type="rpm">
    <name>otherarch-pkg</name>
    <arch>i586</arch>
    <version epoch="0" ver="1.0" rel="1"/>
    <checksum type="sha256" pkgid="YES">pkgid_otherarch</checksum>
    <format>
      <rpm:sourcerpm>otherarch-1.0-1.src.rpm</rpm:sourcerpm>
    </format>
  </package>
</metadata>
"""
        pri_gz = os.path.join(repodata_dir, "primary.xml.gz")
        with gzip.open(pri_gz, "wb") as f:
            f.write(pri_xml.encode("utf-8"))

        cl_xml_parts = []
        for ctime, ctext in changelog_entries:
            cl_xml_parts.append(f'<changelog author="Dev &lt;dev@opensuse.org&gt;" date="{ctime}">{ctext}</changelog>')
        cl_xml = "\n    ".join(cl_xml_parts)

        oth_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<otherdata xmlns="http://linux.duke.edu/metadata/other" packages="5">
  <package pkgid="pkgid_mypkg_{pkg_version}" name="mypkg" arch="x86_64">
    <version epoch="0" ver="{pkg_version}" rel="1.1"/>
    {cl_xml}
  </package>
  <package pkgid="pkgid_mypkg_devel_{pkg_version}" name="mypkg-devel" arch="x86_64">
    <version epoch="0" ver="{pkg_version}" rel="1.1"/>
    {cl_xml}
  </package>
  <package pkgid="pkgid_mypkg_doc_{pkg_version}" name="mypkg-doc" arch="noarch">
    <version epoch="0" ver="{pkg_version}" rel="1.1"/>
    {cl_xml}
  </package>
  <package pkgid="pkgid_kernel" name="kernel-default" arch="x86_64">
    <version epoch="0" ver="6.14.0" rel="1.1"/>
    <changelog author="Kernel Dev &lt;kernel@opensuse.org&gt;" date="1720000000">Kernel update</changelog>
  </package>
  <package pkgid="pkgid_otherarch" name="otherarch-pkg" arch="i586">
    <version epoch="0" ver="1.0" rel="1"/>
    <changelog author="Dev &lt;dev@opensuse.org&gt;" date="1700000000">Other arch</changelog>
  </package>
</otherdata>
"""
        oth_gz = os.path.join(repodata_dir, "other.xml.gz")
        with gzip.open(oth_gz, "wb") as f:
            f.write(oth_xml.encode("utf-8"))

        repomd_xml = """<?xml version="1.0" encoding="UTF-8"?>
<repomd xmlns="http://linux.duke.edu/metadata/repo">
  <data type="primary">
    <location href="repodata/primary.xml.gz"/>
  </data>
  <data type="other">
    <location href="repodata/other.xml.gz"/>
  </data>
</repomd>
"""
        repomd_path = os.path.join(repodata_dir, "repomd.xml")
        with open(repomd_path, "w", encoding="utf-8") as f:
            f.write(repomd_xml)

        return repomd_path

    def test_read_repomd_and_arch_filtering(self):
        repo_dir = os.path.join(self.test_dir, "repo1")
        self._create_synthetic_repo(repo_dir)

        logger = fpn.ChangeLogger()
        # Read with arch=x86_64 (should keep x86_64 and noarch, drop i586)
        pkgdata, changelogs = logger.readChangeLogs([repo_dir], arch="x86_64")

        # Verify packages
        self.assertIn("mypkg", pkgdata)
        self.assertIn("mypkg-devel", pkgdata)
        self.assertIn("mypkg-doc", pkgdata)
        self.assertIn("kernel-default", pkgdata)
        self.assertNotIn("otherarch-pkg", pkgdata)

        # Verify kernel sourcerpm normalization
        self.assertEqual(pkgdata["kernel-default"]["sourcerpm"], "kernel-source-6.14.0-1.1.src.rpm")

        # Verify changelog deduplication and descending sorting
        srpm_mypkg = "mysrc-1.0-1.1.src.rpm"
        self.assertIn(srpm_mypkg, changelogs)
        self.assertCountEqual(changelogs[srpm_mypkg]["packages"], ["mypkg", "mypkg-devel", "mypkg-doc"])

        # Check changelog timestamps are sorted descending: 1710000000 then 1700000000
        self.assertEqual(changelogs[srpm_mypkg]["changelogtime"], [1710000000, 1700000000])
        self.assertEqual(changelogs[srpm_mypkg]["changelogtext"], ["Second change", "First change"])

    def test_do_save_and_do_diff(self):
        repo_v1 = os.path.join(self.test_dir, "repo_v1")
        self._create_synthetic_repo(repo_v1, pkg_version="1.0", changelog_entries=[
            (1700000000, "- Initial release"),
        ])

        repo_v2 = os.path.join(self.test_dir, "repo_v2")
        self._create_synthetic_repo(repo_v2, pkg_version="2.0", changelog_entries=[
            (1700000000, "- Initial release"),
            (1710000000, "- Update to version 2.0"),
        ])

        data_dir = os.path.join(self.test_dir, "snapshots")
        os.makedirs(data_dir)

        app = fpn.ChangeLogger()

        class Opts:
            dir = data_dir
            arch = "x86_64"
            snapshot = "20261028"

        app.do_save("save", Opts(), repo_v1)

        Opts.snapshot = "20261029"
        app.do_save("save", Opts(), repo_v2)

        # Verify saved snapshot files exist
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "20261028")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "20261029")))

        # Test diffing the snapshots
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            class DiffOpts:
                dir = data_dir

            app.do_diff("diff", DiffOpts(), "20261028", "20261029")
            diff_output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertIn("Packages changed:", diff_output)
        self.assertIn("mysrc (1.0 -> 2.0)", diff_output)
        self.assertIn("==== mysrc ====", diff_output)
        self.assertIn("Version update (1.0 -> 2.0)", diff_output)
        self.assertIn("- Update to version 2.0", diff_output)
        self.assertNotIn("- Initial release", diff_output)

    def test_do_diff_subpackages_capping(self):
        data_dir = os.path.join(self.test_dir, "cap_snapshots")
        os.makedirs(data_dir)

        # Create two snapshots with 30 subpackages
        v1_pkgs = {}
        v2_pkgs = {}
        pkg_names = [f"pkg-{i}" for i in range(30)]
        for p in pkg_names:
            v1_pkgs[p] = {'name': p, 'version': '1.0', 'release': '1.1', 'sourcerpm': 'bigsrc-1.0-1.1.src.rpm'}
            v2_pkgs[p] = {'name': p, 'version': '2.0', 'release': '1.1', 'sourcerpm': 'bigsrc-2.0-1.1.src.rpm'}

        v1_cls = {'bigsrc-1.0-1.1.src.rpm': {'packages': pkg_names, 'changelogtime': [1700000000], 'changelogtext': ['- v1']}}
        v2_cls = {
            'bigsrc-2.0-1.1.src.rpm': {'packages': pkg_names, 'changelogtime': [1710000000, 1700000000], 'changelogtext': ['- v2', '- v1']}
        }

        with open(os.path.join(data_dir, "v1"), "wb") as f:
            pickle.dump([fpn.data_version, (v1_pkgs, v1_cls)], f)
        with open(os.path.join(data_dir, "v2"), "wb") as f:
            pickle.dump([fpn.data_version, (v2_pkgs, v2_cls)], f)

        app = fpn.ChangeLogger()
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            class DiffOpts:
                dir = data_dir

            app.do_diff("diff", DiffOpts(), "v1", "v2")
            diff_output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertIn("Subpackages (showing 25 of 30):", diff_output)
        self.assertIn("...", diff_output)

    def test_read_repomd_zstd(self):
        repo_dir = os.path.join(self.test_dir, "repo_zst")
        repodata_dir = os.path.join(repo_dir, "repodata")
        os.makedirs(repodata_dir, exist_ok=True)

        cctx = zstandard.ZstdCompressor()

        pri_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<metadata xmlns="http://linux.duke.edu/metadata/common" xmlns:rpm="http://linux.duke.edu/metadata/rpm" packages="1">
  <package type="rpm">
    <name>zstdpkg</name>
    <arch>x86_64</arch>
    <version epoch="0" ver="1.0" rel="1"/>
    <checksum type="sha256" pkgid="YES">pkgid_zstd</checksum>
    <format>
      <rpm:sourcerpm>zstdsrc-1.0-1.src.rpm</rpm:sourcerpm>
    </format>
  </package>
</metadata>
"""
        with open(os.path.join(repodata_dir, "primary.xml.zst"), "wb") as f:
            f.write(cctx.compress(pri_xml))

        oth_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<otherdata xmlns="http://linux.duke.edu/metadata/other" packages="1">
  <package pkgid="pkgid_zstd" name="zstdpkg" arch="x86_64">
    <version epoch="0" ver="1.0" rel="1"/>
    <changelog author="Dev &lt;dev@opensuse.org&gt;" date="1705000000">Zstd changelog</changelog>
  </package>
</otherdata>
"""
        with open(os.path.join(repodata_dir, "other.xml.zst"), "wb") as f:
            f.write(cctx.compress(oth_xml))

        repomd_xml = """<?xml version="1.0" encoding="UTF-8"?>
<repomd xmlns="http://linux.duke.edu/metadata/repo">
  <data type="primary">
    <location href="repodata/primary.xml.zst"/>
  </data>
  <data type="other">
    <location href="repodata/other.xml.zst"/>
  </data>
</repomd>
"""
        with open(os.path.join(repodata_dir, "repomd.xml"), "w") as f:
            f.write(repomd_xml)

        logger = fpn.ChangeLogger()
        pkgdata, changelogs = logger.readChangeLogs([repo_dir])
        self.assertIn("zstdpkg", pkgdata)
        self.assertEqual(pkgdata["zstdpkg"]["sourcerpm"], "zstdsrc-1.0-1.src.rpm")
        self.assertIn("zstdsrc-1.0-1.src.rpm", changelogs)
        self.assertEqual(changelogs["zstdsrc-1.0-1.src.rpm"]["changelogtext"], ["Zstd changelog"])

    def test_web_diff_with_from_param(self):
        # Load factory-package-news-web
        web_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), '..', 'factory-package-news', 'factory-package-news-web.py')
        )
        spec_web = importlib.util.spec_from_file_location("factory_package_news_web", web_path)
        fpn_web = importlib.util.module_from_spec(spec_web)
        spec_web.loader.exec_module(fpn_web)

        # Create snapshots directory structure inside test_dir
        base_dir = self.test_dir
        snapshots_dir = os.path.join(base_dir, "snapshot-changes", "opensuse", "Tumbleweed")
        os.makedirs(snapshots_dir)

        # Create synthetic repo v1 and v2
        repo_v1 = os.path.join(self.test_dir, "repo_v1")
        self._create_synthetic_repo(repo_v1, pkg_version="1.0", changelog_entries=[(1700000000, "- Initial release")])
        repo_v2 = os.path.join(self.test_dir, "repo_v2")
        self._create_synthetic_repo(
            repo_v2, pkg_version="2.0", changelog_entries=[(1700000000, "- Initial release"), (1710000000, "- Update to 2.0")])

        app_fpn = fpn.ChangeLogger()

        class Opts:
            dir = snapshots_dir
            arch = "x86_64"
            snapshot = "20261020"

        app_fpn.do_save("save", Opts(), repo_v1)
        Opts.snapshot = "20261027"
        app_fpn.do_save("save", Opts(), repo_v2)

        # Set 'current' symlink pointing to 20261020
        os.symlink("20261020", os.path.join(snapshots_dir, "current"))

        # Point web service to our temp base_dir
        fpn_web.BASE_DIR = base_dir
        client = fpn_web.app.test_client()

        # 1. Default without ?from= compares against current (20261020)
        resp = client.get('/snapshot-changes/opensuse/Tumbleweed/diff/20261027')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"mysrc (1.0 -> 2.0)", resp.data)

        # 2. Explicit ?from=20261020
        resp = client.get('/snapshot-changes/opensuse/Tumbleweed/diff/20261027?from=20261020')
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"mysrc (1.0 -> 2.0)", resp.data)

        # 3. Invalid version string -> 400
        resp = client.get('/snapshot-changes/opensuse/Tumbleweed/diff/20261027?from=bad_version!')
        self.assertEqual(resp.status_code, 400)

        # 4. Non-existent from version -> 404
        resp = client.get('/snapshot-changes/opensuse/Tumbleweed/diff/20261027?from=20261001')
        self.assertEqual(resp.status_code, 404)


if __name__ == '__main__':
    unittest.main()
