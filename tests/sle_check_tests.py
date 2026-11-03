import logging
import unittest
import urllib.error
from unittest import mock
from xml.etree import ElementTree as ET

import requests as requests_lib

import sle_check
from sle_check import (
    added_binaries_from_diff,
    build_accepted_message,
    build_binary_source_match,
    build_declined_message,
    build_person_match,
    build_source_map,
    check_user_validity,
    classify_users,
    extract_file_diff,
    extract_users,
    find_orphan_sources,
    find_orphans,
    is_orphan,
    merge_fallback_sources,
    normalize_maintainership,
    parse_added_binaries,
    parse_persons,
    parse_published_sources,
    query_published_sources,
    resolve_sources,
)

SOURCEINFO_XML = """<sourceinfolist>
  <sourceinfo package="libguestfs">
    <subpacks>libguestfs</subpacks>
    <subpacks>libguestfs-appliance</subpacks>
  </sourceinfo>
  <sourceinfo package="systemd">
    <subpacks>systemd</subpacks>
    <subpacks>udev</subpacks>
  </sourceinfo>
  <sourceinfo package="graphviz:qt6">
    <subpacks>graphviz-gd</subpacks>
    <subpacks>graphviz-gnome</subpacks>
  </sourceinfo>
  <sourceinfo package="">
    <subpacks>ignored</subpacks>
  </sourceinfo>
</sourceinfolist>"""

PERSONS_XML = """<collection>
  <person><login>alice</login><state>confirmed</state></person>
  <person><login>bob</login><state>locked</state></person>
  <person><state>confirmed</state></person>
</collection>"""

PUBLISHED_XML = """<collection>
  <binary name="crash-kmp-rt" project="SUSE:SLFO:Main" package="crash"/>
  <binary name="python313-libmodulemd" project="SUSE:SLFO:Main" package="libmodulemd"/>
  <binary name="graphviz-gd" project="SUSE:SLFO:Main" package="graphviz:qt6"/>
  <binary name="crash-kmp-rt" project="SUSE:SLFO:Main:Staging:V" package="crash-staging"/>
  <binary name="dual" project="SUSE:SLFO:Main" package="src-a"/>
  <binary name="dual" project="SUSE:SLFO:Main" package="src-b"/>
  <binary project="SUSE:SLFO:Main" package="no-name"/>
  <binary name="no-package" project="SUSE:SLFO:Main"/>
</collection>"""

MULTI_FILE_DIFF = (
    "diff --git a/other.yaml b/other.yaml\n"
    "--- a/other.yaml\n"
    "+++ b/other.yaml\n"
    "@@\n"
    "+    - not-a-binary\n"
    "diff --git a/000productcompose/default.productcompose"
    " b/000productcompose/default.productcompose\n"
    "--- a/000productcompose/default.productcompose\n"
    "+++ b/000productcompose/default.productcompose\n"
    "@@\n"
    " context\n"
    "+    - libguestfs-appliance\n"
    "+    - systemd # comment\n"
    "+    - graphviz-gd# nospace\n"
    "-    - removed\n"
)


class ParseAddedBinariesTests(unittest.TestCase):
    def test_added_only_and_comment_strip(self):
        patch = " context\n+    - aaa\n+    - bbb # c\n+    - ccc#d\n-    - removed\n"
        self.assertEqual(parse_added_binaries(patch), ["aaa", "bbb", "ccc"])

    def test_dedup_and_sort(self):
        self.assertEqual(
            parse_added_binaries("+    - b\n+    - a\n+    - b\n"), ["a", "b"]
        )

    def test_empty(self):
        self.assertEqual(parse_added_binaries(""), [])


class ExtractFileDiffTests(unittest.TestCase):
    def test_isolates_target_file(self):
        self.assertEqual(
            added_binaries_from_diff(MULTI_FILE_DIFF),
            ["graphviz-gd", "libguestfs-appliance", "systemd"],
        )

    def test_absent_file(self):
        self.assertEqual(
            extract_file_diff(
                "diff --git a/x b/x\n+    - y\n", sle_check.PRODUCTCOMPOSE_PATH
            ),
            "",
        )
        self.assertEqual(added_binaries_from_diff("diff --git a/x b/x\n+    - y\n"), [])

    def test_crlf_line_endings(self):
        crlf_diff = MULTI_FILE_DIFF.replace("\n", "\r\n")
        self.assertEqual(
            added_binaries_from_diff(crlf_diff),
            ["graphviz-gd", "libguestfs-appliance", "systemd"],
        )


class BuildSourceMapTests(unittest.TestCase):
    def setUp(self):
        self.m = build_source_map(ET.fromstring(SOURCEINFO_XML))

    def test_maps_subpacks(self):
        self.assertEqual(self.m["libguestfs-appliance"], "libguestfs")
        self.assertEqual(self.m["udev"], "systemd")

    def test_strips_flavor(self):
        self.assertEqual(self.m["graphviz-gd"], "graphviz")
        self.assertEqual(self.m["graphviz-gnome"], "graphviz")

    def test_skips_empty_package(self):
        self.assertNotIn("ignored", self.m)


class ResolveSourcesTests(unittest.TestCase):
    def test_hits_and_misses(self):
        sm = {"libguestfs-appliance": "libguestfs", "udev": "systemd"}
        self.assertEqual(
            resolve_sources(["libguestfs-appliance", "udev", "nope"], sm),
            (["libguestfs", "systemd"], ["nope"]),
        )

    def test_dedup(self):
        self.assertEqual(resolve_sources(["a", "b"], {"a": "s", "b": "s"}), (["s"], []))


class NormalizeMaintainershipTests(unittest.TestCase):
    def test_one_dot_o(self):
        data = {
            "header": {"document": "obs-maintainers", "version": "1.0"},
            "project": "p",
            "packages": {"foo": {"users": ["a"], "groups": []}},
        }
        self.assertEqual(normalize_maintainership(data)["foo"]["users"], ["a"])

    def test_legacy(self):
        db = normalize_maintainership({"foo": ["a"], "bar": []})
        self.assertEqual(db["foo"], {"users": ["a"], "groups": []})
        self.assertEqual(db["bar"], {"users": [], "groups": []})

    def test_unsupported_version(self):
        with self.assertRaises(ValueError):
            normalize_maintainership(
                {
                    "header": {"document": "obs-maintainers", "version": "2.0"},
                    "project": "p",
                    "packages": {},
                }
            )

    def test_bad_document(self):
        with self.assertRaises(ValueError):
            normalize_maintainership(
                {
                    "header": {"document": "other", "version": "1.0"},
                    "project": "p",
                    "packages": {},
                }
            )


class OrphanPredicateTests(unittest.TestCase):
    def setUp(self):
        self.db = {
            "hu": {"users": ["a"], "groups": []},
            "hg": {"users": [], "groups": ["g"]},
            "e": {"users": [], "groups": []},
            "n": None,
        }

    def test_is_orphan(self):
        self.assertFalse(is_orphan(self.db, "hu"))
        self.assertFalse(is_orphan(self.db, "hg"))
        self.assertTrue(is_orphan(self.db, "e"))
        self.assertTrue(is_orphan(self.db, "n"))
        self.assertTrue(is_orphan(self.db, "missing"))

    def test_find_orphans_sorted(self):
        self.assertEqual(
            find_orphans(["hu", "e", "missing"], self.db), ["e", "missing"]
        )


class ExtractUsersTests(unittest.TestCase):
    def test_unique_sorted_groups_ignored(self):
        db = {
            "a": {"users": ["alice", "bob"], "groups": ["g"]},
            "b": {"users": ["alice"], "groups": []},
        }
        self.assertEqual(extract_users(db), ["alice", "bob"])

    def test_unsafe_login(self):
        with self.assertRaises(ValueError):
            extract_users({"a": {"users": ["bad login"], "groups": []}})

    def test_empty_login(self):
        with self.assertRaises(ValueError):
            extract_users({"a": {"users": [""], "groups": []}})


class PersonTests(unittest.TestCase):
    def test_build_person_match(self):
        self.assertEqual(build_person_match(["a", "b"]), "(@login='a' or @login='b')")

    def test_parse_persons(self):
        self.assertEqual(
            parse_persons(ET.fromstring(PERSONS_XML)),
            {"alice": "confirmed", "bob": "locked"},
        )

    def test_classify_users(self):
        r = classify_users(
            ["alice", "bob", "carol"], {"alice": "confirmed", "bob": "locked"}
        )
        self.assertEqual(r.confirmed, ["alice"])
        self.assertEqual(r.invalid, ["bob"])
        self.assertEqual(r.not_found, ["carol"])


class BuildBinarySourceMatchTests(unittest.TestCase):
    def test_ors_names_and_ands_project(self):
        self.assertEqual(
            build_binary_source_match(["a", "b"], "SUSE:SLFO:Main"),
            "(@name='a' or @name='b') and @project='SUSE:SLFO:Main'",
        )

    def test_single_name(self):
        self.assertEqual(
            build_binary_source_match(["crash-kmp-rt"], "SUSE:SLFO:Main"),
            "(@name='crash-kmp-rt') and @project='SUSE:SLFO:Main'",
        )


class ParsePublishedSourcesTests(unittest.TestCase):
    def setUp(self):
        self.m = parse_published_sources(
            ET.fromstring(PUBLISHED_XML), "SUSE:SLFO:Main"
        )

    def test_maps_binary_to_source(self):
        self.assertEqual(self.m["crash-kmp-rt"], ["crash"])
        self.assertEqual(self.m["python313-libmodulemd"], ["libmodulemd"])

    def test_strips_flavor(self):
        self.assertEqual(self.m["graphviz-gd"], ["graphviz"])

    def test_excludes_other_project_exact_match(self):
        # crash-kmp-rt in SUSE:SLFO:Main:Staging:V must not leak in.
        self.assertEqual(self.m["crash-kmp-rt"], ["crash"])

    def test_groups_multiple_sources_sorted(self):
        self.assertEqual(self.m["dual"], ["src-a", "src-b"])

    def test_skips_rows_missing_attrs(self):
        # Row with no @package (name "no-package") is dropped.
        self.assertNotIn("no-package", self.m)
        # Row with no @name is dropped (no key, and its package never appears).
        self.assertNotIn(None, self.m)
        self.assertNotIn("no-name", [s for sources in self.m.values() for s in sources])


class MergeFallbackSourcesTests(unittest.TestCase):
    def test_folds_resolved_and_reports_still_failed(self):
        sources, still_failed = merge_fallback_sources(
            ["libguestfs"],
            ["crash-kmp-rt", "python313-libmodulemd", "brand-new"],
            {"crash-kmp-rt": ["crash"], "python313-libmodulemd": ["libmodulemd"]},
        )
        self.assertEqual(sources, ["crash", "libguestfs", "libmodulemd"])
        self.assertEqual(still_failed, ["brand-new"])

    def test_dedups_against_existing_sources(self):
        sources, still_failed = merge_fallback_sources(
            ["crash"], ["crash-kmp-rt"], {"crash-kmp-rt": ["crash"]}
        )
        self.assertEqual(sources, ["crash"])
        self.assertEqual(still_failed, [])

    def test_multiple_sources_per_binary(self):
        sources, still_failed = merge_fallback_sources(
            [], ["dual"], {"dual": ["src-a", "src-b"]}
        )
        self.assertEqual(sources, ["src-a", "src-b"])
        self.assertEqual(still_failed, [])

    def test_empty_resolved_leaves_all_failed(self):
        sources, still_failed = merge_fallback_sources(["x"], ["a", "b"], {})
        self.assertEqual(sources, ["x"])
        self.assertEqual(still_failed, ["a", "b"])


class QueryPublishedSourcesTests(unittest.TestCase):
    @staticmethod
    def _fake_xml_parse(_response):
        return ET.ElementTree(ET.fromstring(PUBLISHED_XML))

    def test_batches_by_batch_size(self):
        with (
            mock.patch.object(sle_check, "http_GET") as http_get,
            mock.patch.object(
                sle_check, "xml_parse", side_effect=self._fake_xml_parse
            ),
            mock.patch.object(sle_check, "makeurl", return_value="http://url"),
        ):
            result = query_published_sources(
                "http://api", ["a", "b", "c"], "SUSE:SLFO:Main", batch_size=1
            )
        self.assertEqual(http_get.call_count, 3)
        # Merged across batches, resolved from the fixture.
        self.assertEqual(result["crash-kmp-rt"], ["crash"])

    def test_unsafe_name_never_reaches_query(self):
        captured = []

        def fake_makeurl(_apiurl, _path, query):
            captured.append(query["match"])
            return "http://url"

        with (
            mock.patch.object(sle_check, "http_GET"),
            mock.patch.object(
                sle_check, "xml_parse", side_effect=self._fake_xml_parse
            ),
            mock.patch.object(sle_check, "makeurl", side_effect=fake_makeurl),
        ):
            query_published_sources(
                "http://api",
                ["crash-kmp-rt", "evil' or '1'='1"],
                "SUSE:SLFO:Main",
            )
        joined = " ".join(captured)
        self.assertIn("crash-kmp-rt", joined)
        self.assertNotIn("evil", joined)

    def test_trailing_newline_name_never_reaches_query(self):
        # Python '$' matches before a trailing '\n'; the safety barrier must not rely on that.
        with (
            mock.patch.object(sle_check, "http_GET") as http_get,
            mock.patch.object(sle_check, "xml_parse"),
            mock.patch.object(sle_check, "makeurl"),
        ):
            result = query_published_sources(
                "http://api", ["crash\n"], "SUSE:SLFO:Main"
            )
        http_get.assert_not_called()
        self.assertEqual(result, {})

    def test_all_unsafe_makes_no_request(self):
        with (
            mock.patch.object(sle_check, "http_GET") as http_get,
            mock.patch.object(sle_check, "xml_parse"),
            mock.patch.object(sle_check, "makeurl"),
        ):
            result = query_published_sources(
                "http://api", ["bad'name", ""], "SUSE:SLFO:Main"
            )
        http_get.assert_not_called()
        self.assertEqual(result, {})


class FindOrphanSourcesTests(unittest.TestCase):
    def test_orphans_and_failed(self):
        db = {"libguestfs": {"users": ["a"], "groups": []}}
        result = find_orphan_sources(
            ["libguestfs-appliance", "udev", "nobin"], ET.fromstring(SOURCEINFO_XML), db
        )
        self.assertEqual(result.orphans, ["systemd"])
        self.assertEqual(result.failed, ["nobin"])
        self.assertEqual(result.checked, 2)

    def test_fallback_resolves_dynamic_binary(self):
        db = {"libguestfs": {"users": ["a"], "groups": []}}
        seen = {}

        def resolver(names):
            seen["names"] = list(names)
            return {"crash-kmp-rt": ["crash"]}

        result = find_orphan_sources(
            ["libguestfs-appliance", "crash-kmp-rt", "brand-new"],
            ET.fromstring(SOURCEINFO_XML),
            db,
            fallback_resolver=resolver,
        )
        # Resolver is asked only for the binaries view=info could not resolve.
        self.assertEqual(sorted(seen["names"]), ["brand-new", "crash-kmp-rt"])
        # crash-kmp-rt now resolves to orphan source "crash".
        self.assertEqual(result.orphans, ["crash"])
        # brand-new remains genuinely unresolved.
        self.assertEqual(result.failed, ["brand-new"])
        self.assertEqual(result.checked, 2)

    def test_fallback_exception_propagates(self):
        # Fail-closed: a fallback error must surface (to check_source_submission's handler),
        # never be swallowed into a pass that lets an orphan through this gating bot.
        def resolver(names):
            raise urllib.error.HTTPError("u", 500, "err", {}, None)

        with self.assertRaises(urllib.error.HTTPError):
            find_orphan_sources(
                ["brand-new"],
                ET.fromstring(SOURCEINFO_XML),
                {},
                fallback_resolver=resolver,
            )

    def test_fallback_not_called_when_all_resolved(self):
        db = {"libguestfs": {"users": ["a"], "groups": []}, "systemd": {"users": ["a"], "groups": []}}
        called = []
        result = find_orphan_sources(
            ["libguestfs-appliance", "udev"],
            ET.fromstring(SOURCEINFO_XML),
            db,
            fallback_resolver=lambda names: called.append(names) or {},
        )
        self.assertEqual(called, [])
        self.assertEqual(result.failed, [])


class CheckUserValidityTests(unittest.TestCase):
    def _fake_query(self, apiurl, batch):
        table = {"alice": "confirmed", "bob": "locked"}
        return {u: table[u] for u in batch if u in table}

    def test_aggregates(self):
        db = {
            "p1": {"users": ["alice", "carol"], "groups": []},
            "p2": {"users": ["bob"], "groups": []},
        }
        with mock.patch.object(
            sle_check, "query_persons", side_effect=self._fake_query
        ):
            r = check_user_validity(db, "http://api")
        self.assertEqual(r.confirmed, ["alice"])
        self.assertEqual(r.invalid, ["bob"])
        self.assertEqual(r.not_found, ["carol"])

    def test_batching(self):
        db = {"p1": {"users": ["alice", "bob", "carol"], "groups": []}}
        with mock.patch.object(
            sle_check, "query_persons", side_effect=self._fake_query
        ) as q:
            check_user_validity(db, "http://api", batch_size=1)
        self.assertEqual(q.call_count, 3)


class MessageTests(unittest.TestCase):
    def test_declined(self):
        self.assertEqual(
            build_declined_message(["src1"], ["bob"], ["carol"]),
            "New source packages with no maintainer in _maintainership.json:\n\n- src1\n\n"
            "Maintainer logins not found in OBS:\n\n- carol\n\n"
            "Maintainer logins that are not confirmed OBS accounts:\n\n- bob",
        )

    def test_accepted_clean(self):
        self.assertEqual(build_accepted_message([]), "Maintainership check passed.")

    def test_accepted_with_unresolved(self):
        msg = build_accepted_message(["x"])
        self.assertIn("could not be resolved", msg)
        self.assertIn("- x", msg)


class _Req:
    def __init__(self):
        self.actions = [mock.Mock(src_rev="HEAD")]
        self._owner = "products"
        self._repo = "SLES"
        self._pr_id = 1


def _make_bot(allowed=("products/SLES",)):
    bot = object.__new__(sle_check.SleCheckBot)
    bot.logger = logging.getLogger("test")
    bot.review_messages = {}
    bot.allowed_repositories = list(allowed)
    bot.apiurl = "http://api"
    bot.platform = mock.Mock()
    bot.requests = [_Req()]
    return bot


class RunCheckTests(unittest.TestCase):
    def test_not_allowed_repo(self):
        self.assertEqual(
            _make_bot(allowed=[]).run_check("o", "p", "HEAD", "to", "tp"), (None, None)
        )

    def test_decline_aggregated(self):
        db = {"libguestfs": {"users": ["alice"], "groups": []}}
        with (
            mock.patch.object(sle_check, "fetch_maintainership", return_value=db),
            mock.patch.object(
                sle_check,
                "check_user_validity",
                return_value=sle_check.UserCheckResult([], [], ["carol"]),
            ),
            mock.patch.object(sle_check, "fetch_pr_diff", return_value=MULTI_FILE_DIFF),
            mock.patch.object(
                sle_check,
                "fetch_source_info",
                return_value=ET.fromstring(SOURCEINFO_XML),
            ),
        ):
            bot = _make_bot()
            result, message = bot.run_check("o", "p", "HEAD", "to", "tp")
        self.assertFalse(result)
        self.assertIsNone(message)
        self.assertIn("systemd", bot.review_messages["declined"])
        self.assertIn("graphviz", bot.review_messages["declined"])
        self.assertIn("carol", bot.review_messages["declined"])

    def test_decline_via_published_fallback(self):
        # crash-kmp-rt is a build-time KMP name absent from the view=info map; only the
        # published-binary fallback resolves it, to orphan source "crash".
        diff = (
            "diff --git a/000productcompose/default.productcompose"
            " b/000productcompose/default.productcompose\n"
            "--- a/000productcompose/default.productcompose\n"
            "+++ b/000productcompose/default.productcompose\n"
            "@@\n"
            "+    - crash-kmp-rt\n"
        )
        with (
            mock.patch.object(sle_check, "fetch_maintainership", return_value={}),
            mock.patch.object(
                sle_check,
                "check_user_validity",
                return_value=sle_check.UserCheckResult([], [], []),
            ),
            mock.patch.object(sle_check, "fetch_pr_diff", return_value=diff),
            mock.patch.object(
                sle_check,
                "fetch_source_info",
                return_value=ET.fromstring(SOURCEINFO_XML),
            ),
            mock.patch.object(
                sle_check,
                "query_published_sources",
                return_value={"crash-kmp-rt": ["crash"]},
            ) as qps,
        ):
            bot = _make_bot()
            result, message = bot.run_check("o", "p", "HEAD", "to", "tp")
        self.assertFalse(result)
        self.assertIsNone(message)
        self.assertIn("crash", bot.review_messages["declined"])
        qps.assert_called_once_with(bot.apiurl, ["crash-kmp-rt"], sle_check.SOURCE_PROJECT)

    def test_accept_clean_untouched(self):
        with (
            mock.patch.object(sle_check, "fetch_maintainership", return_value={}),
            mock.patch.object(
                sle_check,
                "check_user_validity",
                return_value=sle_check.UserCheckResult([], [], []),
            ),
            mock.patch.object(
                sle_check,
                "fetch_pr_diff",
                return_value="diff --git a/x b/x\n+    - y\n",
            ),
            mock.patch.object(sle_check, "fetch_source_info") as fsi,
        ):
            bot = _make_bot()
            result, message = bot.run_check("o", "p", "HEAD", "to", "tp")
        self.assertTrue(result)
        self.assertEqual(message, "Maintainership check passed.")
        fsi.assert_not_called()


class CheckSourceSubmissionTests(unittest.TestCase):
    def test_transient_requests(self):
        bot = _make_bot()
        bot.run_check = mock.Mock(side_effect=requests_lib.exceptions.ConnectionError())
        self.assertIsNone(bot.check_source_submission("o", "p", "r", "to", "tp"))

    def test_transient_urllib(self):
        bot = _make_bot()
        bot.run_check = mock.Mock(side_effect=urllib.error.URLError("x"))
        self.assertIsNone(bot.check_source_submission("o", "p", "r", "to", "tp"))

    def test_unexpected_declines(self):
        bot = _make_bot()
        bot.run_check = mock.Mock(side_effect=RuntimeError("boom"))
        self.assertFalse(bot.check_source_submission("o", "p", "r", "to", "tp"))
        self.assertIn("Unhandled exception", bot.review_messages["declined"])

    def test_success_accepts(self):
        bot = _make_bot()
        bot.run_check = mock.Mock(return_value=(True, "Maintainership check passed."))
        self.assertTrue(bot.check_source_submission("o", "p", "r", "to", "tp"))
        self.assertEqual(
            bot.review_messages["accepted"], "Maintainership check passed."
        )


if __name__ == "__main__":
    unittest.main()
