# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit and consistency tests for generate_conformance_matrix."""

import argparse
import contextlib
import io
import json
import os
import tempfile

import unittest as absltest

import generate_conformance_matrix as gcm


def _find_spec_root_dir() -> str:
  test_srcdir = (
      absltest.TEST_SRCDIR.value
      if hasattr(absltest, "TEST_SRCDIR")
      else os.environ.get("TEST_SRCDIR", "")
  )
  if test_srcdir:
    workspace = os.environ.get("TEST_WORKSPACE", "google3")
    g3_spec = os.path.join(test_srcdir, workspace, "third_party/cel/spec")
    if os.path.isdir(g3_spec):
      return g3_spec
  return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


class GenerateConformanceMatrixTest(absltest.TestCase):

  def test_expand_and_match_skip_patterns(self):
    raw = [
        "  ",
        '"enums/strong_proto2"',
        (
            "comparisons/eq_literal/eq_mixed_types_error,"
            "eq_list_elem_mixed_types_error"
        ),
        "top_a,top_b",
    ]
    expanded = gcm.expand_skip_patterns(raw)
    self.assertEqual(
        expanded,
        [
            "enums/strong_proto2",
            "comparisons/eq_literal/eq_mixed_types_error",
            "comparisons/eq_literal/eq_list_elem_mixed_types_error",
            "top_a",
            "top_b",
        ],
    )
    corpus: list[gcm.SuiteCorpus] = [
        (
            "enums",
            "enums.textproto",
            [
                ("strong_proto2", ["test1"]),
                ("strong_proto2_extra", ["test1"]),
            ],
        ),
        (
            "comparisons",
            "comparisons.textproto",
            [(
                "eq_literal",
                [
                    "eq_mixed_types_error",
                    "eq_list_elem_mixed_types_error",
                    "other_test",
                ],
            )],
        ),
    ]
    report = gcm.synthesize_report_from_corpus(
        runtime="go",
        config="default",
        runtime_version="HEAD",
        spec_commit="HEAD",
        testdata_suites=corpus,
        enabled_files={"enums.textproto", "comparisons.textproto"},
        configured_skips=expanded,
    )
    enums_suite = report.get_suite("enums")
    self.assertIsNotNone(enums_suite)
    assert enums_suite is not None
    sec1 = enums_suite.get_section("strong_proto2")
    self.assertIsNotNone(sec1)
    assert sec1 is not None
    # enums/strong_proto2 is skipped because of expanded[0]
    self.assertEqual(sec1.tests[0].status, "skipped")

    sec2 = enums_suite.get_section("strong_proto2_extra")
    self.assertIsNotNone(sec2)
    assert sec2 is not None
    # enums/strong_proto2_extra is NOT skipped by enums/strong_proto2 prefix
    self.assertEqual(sec2.tests[0].status, "pass")

    comp_suite = report.get_suite("comparisons")
    self.assertIsNotNone(comp_suite)
    assert comp_suite is not None
    comp_sec = comp_suite.get_section("eq_literal")
    self.assertIsNotNone(comp_sec)
    assert comp_sec is not None
    # comparisons/eq_literal/eq_mixed_types_error is skipped by expanded[1]
    self.assertEqual(comp_sec.tests[0].status, "skipped")
    # comparisons/eq_literal/eq_list_elem_mixed_types_error is skipped by
    # expanded[2]
    self.assertEqual(comp_sec.tests[1].status, "skipped")
    # comparisons/eq_literal/other_test is NOT skipped
    self.assertEqual(comp_sec.tests[2].status, "pass")
    # validate_skips catches top_a and top_b which are not in corpus
    skip_errors = gcm.validate_skips(report, testdata_suites=corpus)
    self.assertEqual(len(skip_errors), 2)
    self.assertIn("top_a", skip_errors[0])
    self.assertIn("top_b", skip_errors[1])

  def test_parse_simple_textproto_content_edge_cases(self):
    content = """
      section {
        name: "sec_a"
        test { name: "t1" }
        test {}
      }
      section {
        test { name: "t_unnamed_sec" }
      }
      section {
        name: "sec_a"
        test { name: "t2" }
      }
    """
    suite_name, filename, sections = gcm.parse_simple_textproto_content(
        content, "fallback_suite.textproto"
    )
    self.assertEqual(suite_name, "fallback_suite")
    self.assertEqual(filename, "fallback_suite.textproto")
    self.assertEqual(
        sections,
        [
            ("sec_a", ["t1", "unnamed", "t2"]),
            ("unnamed", ["t_unnamed_sec"]),
        ],
    )

  def test_load_testdata_corpus(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      with open(
          os.path.join(tmpdir, "z_suite.textproto"), "w", encoding="utf-8"
      ) as f:
        f.write('name: "zeta"\nsection { name: "s1" test { name: "t1" } }\n')
      with open(
          os.path.join(tmpdir, "a_suite.textproto"), "w", encoding="utf-8"
      ) as f:
        f.write('name: "alpha"\nsection { name: "s1" test { name: "t1" } }\n')
      with open(os.path.join(tmpdir, "README.md"), "w", encoding="utf-8") as f:
        f.write("ignored file\n")

      corpus = gcm.load_testdata_corpus(tmpdir)
      self.assertEqual(
          corpus,
          [
              ("alpha", "a_suite.textproto", [("s1", ["t1"])]),
              ("zeta", "z_suite.textproto", [("s1", ["t1"])]),
          ],
      )

  def test_load_manifest_and_load_notes(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      manifest_path = os.path.join(tmpdir, "manifest.yaml")
      with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("""
excluded_sections:
  - legacy/sec
excluded_tests:
  - legacy/sec2/t_old
columns:
  - id: go_default
    runtime: go
    config: default
    label: Go
    runtime_version: v0.27.0
    spec_commit: abc1234
    disabled_suites:
      - block_ext.textproto
    configured_skips:
      - enums/strong_proto2
    absent_suites:
      - future.textproto
    absent_sections:
      - legacy/sec
      - custom/absent_sec
    absent_tests:
      - custom/sec/t_absent
""")
      cols = gcm.load_manifest(manifest_path)
      self.assertEqual(len(cols), 1)
      self.assertEqual(cols[0].id, "go_default")
      self.assertEqual(cols[0].disabled_suites, ("block_ext.textproto",))
      self.assertEqual(cols[0].configured_skips, ("enums/strong_proto2",))
      self.assertEqual(cols[0].absent_suites, ("future.textproto",))
      self.assertEqual(
          cols[0].absent_sections, ("legacy/sec", "custom/absent_sec")
      )
      self.assertEqual(
          cols[0].absent_tests, ("custom/sec/t_absent", "legacy/sec2/t_old")
      )

      notes_path = os.path.join(tmpdir, "notes.yaml")
      with open(notes_path, "w", encoding="utf-8") as f:
        f.write("""
notes:
  simple/sec: "Simple string note"
  mapped/all_reg:
    note: "Shared note"
    allow_regression: true
    columns:
      go_default: "Go column note"
  mapped/list_reg:
    allow_regression:
      - go_default
      - cpp_modern
callouts:
  - title: "Note Title"
    body: "Note Body"
""")
      notes_cfg = gcm.load_notes(notes_path)
      self.assertEqual(
          notes_cfg.notes,
          {
              "simple/sec": "Simple string note",
              "mapped/all_reg": "Shared note",
          },
      )
      self.assertEqual(
          notes_cfg.column_notes,
          {("mapped/all_reg", "go_default"): "Go column note"},
      )
      self.assertEqual(
          notes_cfg.allowed_regressions,
          {
              "mapped/all_reg": {"*"},
              "mapped/list_reg": {"go_default", "cpp_modern"},
          },
      )
      self.assertEqual(
          notes_cfg.callouts, [{"title": "Note Title", "body": "Note Body"}]
      )

  def test_cell_status_precedence_and_dashboard_disabled_suites(self):
    report = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="fe86fd9",
        configured_skips=("enabled_suite/skipped_sec/t2",),
        suites=(
            gcm.SuiteReport(
                name="enabled_suite",
                file="enabled_suite.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="pass_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "pass"),
                        ),
                    ),
                    gcm.SectionReport(
                        name="skipped_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "skipped", observed="fail"),
                        ),
                    ),
                    gcm.SectionReport(
                        name="fail_and_skip_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "skipped"),
                            gcm.TestResult("t3", "fail"),
                        ),
                    ),
                    gcm.SectionReport(
                        name="not_run_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "not_run"),
                        ),
                    ),
                ),
            ),
            gcm.SuiteReport(
                name="enabled_clean_suite",
                file="enabled_clean_suite.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "pass"),
                        ),
                    ),
                    gcm.SectionReport(
                        name="sec2",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "skipped"),
                        ),
                    ),
                ),
            ),
            gcm.SuiteReport(
                name="disabled_unexecuted",
                file="disabled_unexecuted.textproto",
                enabled=False,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(
                            gcm.TestResult("t1", "not_enabled"),
                            gcm.TestResult("t2", "not_enabled"),
                        ),
                    ),
                ),
            ),
            gcm.SuiteReport(
                name="disabled_executed",
                file="disabled_executed.textproto",
                enabled=False,
                sections=(
                    gcm.SectionReport(
                        name="passing_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "pass"),
                        ),
                    ),
                    gcm.SectionReport(
                        name="skipped_only_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "skipped"),
                        ),
                    ),
                    gcm.SectionReport(
                        name="failing_sec",
                        tests=(
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "fail"),
                        ),
                    ),
                ),
            ),
            gcm.SuiteReport(
                name="empty_suite",
                file="empty_suite.textproto",
                enabled=True,
                sections=(),
            ),
        ),
    )

    cases = [
        ("missing_from_corpus", report, "nonexistent", "sec", "na", "`n/a`"),
        (
            "missing_section_in_suite",
            report,
            "enabled_suite",
            "nonexistent_sec",
            "na",
            "`n/a`",
        ),
        (
            "empty_suite_wildcard",
            report,
            "empty_suite",
            "*",
            "na",
            "`n/a`",
        ),
        ("missing_report", None, "enabled_suite", "pass_sec", "not_run", "❓"),
        (
            "disabled_unexecuted",
            report,
            "disabled_unexecuted",
            "sec1",
            "not_enabled",
            "⬜",
        ),
        (
            "disabled_unexecuted_wildcard",
            report,
            "disabled_unexecuted",
            "*",
            "not_enabled",
            "⬜",
        ),
        (
            "disabled_executed_pass",
            report,
            "disabled_executed",
            "passing_sec",
            "not_enabled",
            "⬜ (✅ 2/2)",
        ),
        (
            "disabled_executed_skip",
            report,
            "disabled_executed",
            "skipped_only_sec",
            "not_enabled",
            "⬜ (⚠️ 1/2)",
        ),
        (
            "disabled_executed_fail",
            report,
            "disabled_executed",
            "failing_sec",
            "not_enabled",
            "⬜ (❌ 1/2)",
        ),
        (
            "disabled_executed_wildcard",
            report,
            "disabled_executed",
            "*",
            "not_enabled",
            "⬜ (❌ 4/6)",
        ),
        (
            "not_run_in_section",
            report,
            "enabled_suite",
            "not_run_sec",
            "not_run",
            "❓",
        ),
        (
            "enabled_suite_wildcard_not_run",
            report,
            "enabled_suite",
            "*",
            "not_run",
            "❓",
        ),
        (
            "enabled_clean_suite_wildcard",
            report,
            "enabled_clean_suite",
            "*",
            "skipped",
            "⚠️ 3/4",
        ),
        (
            "fail_precedence_over_skip",
            report,
            "enabled_suite",
            "fail_and_skip_sec",
            "fail",
            "❌ 1/3",
        ),
        (
            "skipped_section",
            report,
            "enabled_suite",
            "skipped_sec",
            "skipped",
            "⚠️ 1/2",
        ),
        (
            "passed_section",
            report,
            "enabled_suite",
            "pass_sec",
            "pass",
            "✅ 2/2",
        ),
    ]
    for (
        case_name,
        rep,
        suite_name,
        sec_name,
        expected_cat,
        expected_str,
    ) in cases:
      with self.subTest(case=case_name):
        cell = gcm.resolve_section_cell(rep, suite_name, sec_name)
        self.assertEqual(cell.category, expected_cat)
        self.assertEqual(cell.rendered, expected_str)

  def test_resolve_disabled_tests_mixed_observed_outcomes(self):
    report = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="fe86fd9",
        configured_skips=(),
        suites=(
            gcm.SuiteReport(
                name="disabled_suite",
                file="disabled_suite.textproto",
                enabled=False,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(
                            gcm.TestResult("t1", "skipped", observed="pass"),
                            gcm.TestResult("t2", "skipped", observed="fail"),
                        ),
                    ),
                ),
            ),
        ),
    )
    status = gcm.resolve_section_cell(report, "disabled_suite", "sec1")
    self.assertEqual(status.category, "not_enabled")
    self.assertEqual(status.rendered, "⬜ (❌ 1/2)")
    self.assertEqual(status.passed, 1)
    self.assertEqual(status.total, 2)

  def test_wildcard_collapse_only_when_all_columns_unexecuted_disabled(self):
    manifest = [
        gcm.ManifestColumn(id="go", runtime="go", config="default", label="Go"),
        gcm.ManifestColumn(
            id="java", runtime="java", config="default", label="Java"
        ),
    ]
    unexecuted_suite = gcm.SuiteReport(
        name="network_ext",
        file="network_ext.textproto",
        enabled=False,
        sections=(
            gcm.SectionReport("sec1", (gcm.TestResult("t1", "not_enabled"),)),
            gcm.SectionReport("sec2", (gcm.TestResult("t2", "not_enabled"),)),
        ),
    )
    executed_disabled_suite = gcm.SuiteReport(
        name="network_ext",
        file="network_ext.textproto",
        enabled=False,
        sections=(
            gcm.SectionReport("sec1", (gcm.TestResult("t1", "pass"),)),
            gcm.SectionReport("sec2", (gcm.TestResult("t2", "pass"),)),
        ),
    )

    # When both columns have unexecuted disabled suite -> collapses to
    # ("network_ext", "*")
    _, rows_collapsed, _ = gcm.render_markdown(
        manifest=manifest,
        reports={
            ("go", "default"): gcm.ConformanceReport(
                "go", "default", "HEAD", "HEAD", (), (unexecuted_suite,)
            ),
            ("java", "default"): gcm.ConformanceReport(
                "java", "default", "HEAD", "HEAD", (), (unexecuted_suite,)
            ),
        },
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    self.assertEqual(rows_collapsed, [("network_ext", "*")])

    # When one column executes the disabled suite -> expands to individual
    # sections
    _, rows_expanded, _ = gcm.render_markdown(
        manifest=manifest,
        reports={
            ("go", "default"): gcm.ConformanceReport(
                "go", "default", "HEAD", "HEAD", (), (executed_disabled_suite,)
            ),
            ("java", "default"): gcm.ConformanceReport(
                "java", "default", "HEAD", "HEAD", (), (unexecuted_suite,)
            ),
        },
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    self.assertEqual(
        rows_expanded, [("network_ext", "sec1"), ("network_ext", "sec2")]
    )

  def test_synthesize_reports_from_manifest_handles_nested_skips_and_absent(
      self,
  ):
    corpus = [
        (
            "basic",
            "basic.textproto",
            [
                ("sec1", ["sub/t1", "other", "absent_t"]),
                ("absent_sec", ["t2"]),
            ],
        ),
        (
            "disabled_suite",
            "disabled_suite.textproto",
            [("sec_d", ["td"])],
        ),
        (
            "absent_suite",
            "absent_suite.textproto",
            [("sec_a", ["ta"])],
        ),
    ]
    manifest = [
        gcm.ManifestColumn(
            id="go",
            runtime="go",
            config="default",
            label="Go",
            configured_skips=("basic/sec1/sub",),
            disabled_suites=("disabled_suite.textproto",),
            absent_suites=("absent_suite.textproto",),
            absent_sections=("basic/absent_sec",),
            absent_tests=("basic/sec1/absent_t",),
        )
    ]
    reports = gcm.synthesize_reports_from_manifest(manifest, corpus)
    rep = reports[("go", "default")]
    suite_by_name = {s.name: s for s in rep.suites}
    self.assertNotIn("absent_suite", suite_by_name)
    self.assertFalse(suite_by_name["disabled_suite"].enabled)
    basic_secs = {s.name: s for s in suite_by_name["basic"].sections}
    self.assertNotIn("absent_sec", basic_secs)
    test_statuses = {t.name: t.status for t in basic_secs["sec1"].tests}
    self.assertEqual(
        test_statuses,
        {"sub/t1": "skipped", "other": "pass"},
    )

    dash_rep = gcm.synthesize_report_from_corpus(
        runtime="go",
        config="default",
        runtime_version="v1",
        spec_commit="c1",
        testdata_suites=corpus[:2],
        enabled_files={"basic.textproto"},
        configured_skips=("disabled_suite/sec_d/td",),
        dashboard_executed_disabled=True,
    )
    dash_suites = {s.name: s for s in dash_rep.suites}
    self.assertFalse(dash_suites["disabled_suite"].enabled)
    self.assertEqual(
        dash_suites["disabled_suite"].sections[0].tests[0].status, "skipped"
    )

  def test_lookup_note_and_render_markdown_with_callouts_and_synthesized(self):
    manifest = [
        gcm.ManifestColumn(
            id="go_default",
            runtime="go",
            config="default",
            label="Go",
            absent_sections=("basic/excluded_sec",),
            excluded_sections=("basic/excluded_sec",),
        ),
        gcm.ManifestColumn(
            id="cpp_modern",
            runtime="cpp",
            config="modern",
            label="C++",
            absent_sections=("basic/excluded_sec",),
            excluded_sections=("basic/excluded_sec",),
        ),
    ]
    notes_cfg = gcm.NotesConfig(
        notes={
            "basic/sec1": "Shared sec1 note",
            "wildcard_suite/*": "Wildcard note",
        },
        column_notes={
            ("basic/sec1", "go_default"): "Go sec1 note",
            ("basic/sec1", "cpp"): "C++ sec1 note",
            ("wildcard_suite/*", "go"): "Go wildcard col note",
        },
        allowed_regressions={},
        callouts=[{
            "title": "Known Issue",
            "runtimes": "Go, C++",
            "tracking": "issue/1",
            "details": "Detailed explanation.",
        }],
    )
    # Passing cell ignores column note; skipped cell includes column note.
    cells = [
        gcm.CellStatus("skipped", "⚠️ 1/2", 1, 2),
        gcm.CellStatus("pass", "✅ 2/2", 2, 2),
    ]
    note_str = gcm.lookup_note(
        notes_cfg.notes,
        "basic",
        "sec1",
        notes_cfg.column_notes,
        manifest,
        cells,
    )
    self.assertEqual(note_str, "Shared sec1 note; Go sec1 note")

    wildcard_note_str = gcm.lookup_note(
        notes_cfg.notes,
        "wildcard_suite",
        "*",
        notes_cfg.column_notes,
        manifest,
        cells,
    )
    self.assertEqual(wildcard_note_str, "Wildcard note; Go wildcard col note")

    testdata_suites = [
        (
            "basic",
            "basic.textproto",
            [
                ("sec1", ["t1", "t2"]),
                ("sec_pass", ["t3"]),
                ("excluded_sec", ["t_ex"]),
            ],
        ),
    ]
    go_report = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="abc1234",
        configured_skips=("basic/sec1/t2",),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        "sec1",
                        (
                            gcm.TestResult("t1", "pass"),
                            gcm.TestResult("t2", "skipped"),
                        ),
                    ),
                    gcm.SectionReport(
                        "sec_pass",
                        (gcm.TestResult("t3", "pass"),),
                    ),
                    gcm.SectionReport(
                        "excluded_sec",
                        (gcm.TestResult("t_ex", "pass"),),
                    ),
                    gcm.SectionReport(
                        "report_only_sec",
                        (gcm.TestResult("t_rep", "pass"),),
                    ),
                ),
            ),
        ),
    )
    md, rows, _ = gcm.render_markdown(
        manifest=manifest,
        reports={("go", "default"): go_report},
        notes_config=notes_cfg,
        testdata_suites=testdata_suites,
        synthesized=True,
    )
    self.assertNotIn(("basic", "excluded_sec"), rows)
    self.assertIn(("basic", "report_only_sec"), rows)
    self.assertIn("This matrix is derived from each runtime release", md)
    self.assertIn("### Non-Conforming Suites & Known Issues", md)
    self.assertIn("**Known Issue**", md)
    self.assertIn("| **Runtime Version** | — | `v0.27.0` | `n/a` |", md)

  def test_normalize_markdown_preserves_bold_wrapped_prose_in_bullets(self):
    md_wrapped_before_bold = (
        "<!-- comment -->\n"
        "# Heading\n\n"
        "Paragraph line one\nline two.\n\n"
        "* Item with wrapped\n  **bold continuation** text.\n\n"
        "-----\n\n"
        "| Col1 | Col2 | Col3 |\n"
        "| :--- | :---: | ---: |\n"
        "| a | b | c |\n\n"
        "> **Callout**\n"
        ">\n"
        "> *   Bullet line 1\n"
        ">     line 2\n"
        "> Plain quote line 1\n"
        "> line 2\n"
    )
    md_wrapped_after_bold = (
        "<!-- comment -->\n"
        "# Heading\n\n"
        "Paragraph line one line two.\n\n"
        "* Item with wrapped **bold\n  continuation** text.\n\n"
        "---\n\n"
        "| Col1 | Col2 | Col3 |\n"
        "| :--- | :---: | ---: |\n"
        "|  a  |  b  |  c  |\n\n"
        "> **Callout**\n"
        ">\n"
        "> * Bullet line 1 line 2\n"
        "> Plain quote line 1\n"
        "> line 2\n"
    )
    self.assertEqual(
        gcm.normalize_markdown(md_wrapped_before_bold),
        gcm.normalize_markdown(md_wrapped_after_bold),
    )

  def test_validate_skips_catches_stale_skip(self):
    report_with_stale_skip = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="fe86fd9",
        configured_skips=("basic/sec1/nonexistent_test",),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(gcm.TestResult("t1", "skipped"),),
                    ),
                ),
            ),
        ),
    )
    skip_errors = gcm.validate_skips(report_with_stale_skip)
    self.assertEqual(len(skip_errors), 1)
    self.assertIn("nonexistent_test", skip_errors[0])

    # Skip patterns referencing absent suites, sections, or tests (or present
    # in testdata_suites) are accepted when manifest_column or testdata_suites
    # is provided.
    col_with_absent = gcm.ManifestColumn(
        id="cpp",
        runtime="cpp",
        config="modern",
        label="C++",
        absent_suites=("lists_ext.textproto",),
        absent_sections=("namespace/namespace_shadowing",),
        absent_tests=(
            "basic/functions/unbound/nested",
            "legacy_short_test_key",
        ),
    )
    report_with_absent_skips = gcm.ConformanceReport(
        runtime="cpp",
        config="modern",
        runtime_version="v0.16.1",
        spec_commit="0.25.1",
        configured_skips=(
            "lists_ext/sort/int_list",
            "namespace/namespace_shadowing/basic",
            "basic/functions/unbound",
            "legacy_short_test_key",
            "testdata_only/sec1/sub/t1",
        ),
        suites=(),
    )
    testdata_suites = [
        (
            "testdata_only",
            "testdata_only.textproto",
            [("sec1", ["sub/t1"]), ("empty_sec", [])],
        ),
    ]
    self.assertEqual(
        gcm.validate_skips(
            report_with_absent_skips,
            testdata_suites=testdata_suites,
            manifest_column=col_with_absent,
        ),
        [],
    )
    corpus_prefixes = gcm.collect_corpus_prefixes(testdata_suites)
    self.assertEqual(
        gcm.validate_skips(
            report_with_absent_skips,
            corpus_prefixes=corpus_prefixes,
            manifest_column=col_with_absent,
        ),
        [],
    )

  def test_validate_notes_catches_missing_note_and_internal_bug_link(self):
    manifest = [
        gcm.ManifestColumn(id="go", runtime="go", config="default", label="Go"),
    ]
    skipped_report = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="fe86fd9",
        configured_skips=("basic/sec1/t1",),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(gcm.TestResult("t1", "skipped"),),
                    ),
                ),
            ),
        ),
    )
    _, _, rendered_cells = gcm.render_markdown(
        manifest=manifest,
        reports={("go", "default"): skipped_report},
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    note_errors = gcm.validate_notes(
        rendered_cells,
        {"other/sec": "See b/123456"},
        column_notes={("other/sec", "go"): "Internal b/654321"},
        callouts=[{"title": "Alert", "body": "Ref b/999999"}],
    )
    self.assertEqual(len(note_errors), 4)
    self.assertIn("basic/sec1", note_errors[0])
    self.assertIn("Internal bug link in note", note_errors[1])
    self.assertIn("Internal bug link in column note", note_errors[2])
    self.assertIn("Internal bug link in callout", note_errors[3])

  def test_validate_coverage_catches_missing_row(self):
    cov_errors = gcm.validate_coverage(
        expected_sections=[("basic", "sec1"), ("basic", "sec2")],
        rendered_rows=[("basic", "sec1")],
    )
    self.assertEqual(len(cov_errors), 1)
    self.assertIn("basic/sec2", cov_errors[0])

  def test_validate_no_regressions_and_allowlist(self):
    manifest = [
        gcm.ManifestColumn(id="go", runtime="go", config="default", label="Go"),
    ]
    regressed_report = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="fe86fd9",
        configured_skips=("basic/sec1/t1",),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(gcm.TestResult("t1", "skipped"),),
                    ),
                ),
            ),
        ),
    )
    passing_baseline = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.26.0",
        spec_commit="fe86fd9",
        configured_skips=(),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(gcm.TestResult("t1", "pass"),),
                    ),
                ),
            ),
        ),
    )
    _, _, rendered_cells = gcm.render_markdown(
        manifest=manifest,
        reports={("go", "default"): regressed_report},
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    baseline_md, _, _ = gcm.render_markdown(
        manifest=manifest,
        reports={("go", "default"): passing_baseline},
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    md_reg_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest,
        rendered_cells=rendered_cells,
        baseline_markdown=baseline_md,
        allowed_regressions={},
    )
    self.assertEqual(len(md_reg_errors), 1)
    self.assertIn("Regression in 'basic/sec1'", md_reg_errors[0])

    # Missing current report for a column resolves to ❓ (not_run) and is
    # flagged as a regression.
    _, _, missing_curr_cells = gcm.render_markdown(
        manifest=manifest,
        reports={},
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
        testdata_suites=[("basic", "basic.textproto", [("sec1", ["t1"])])],
    )
    missing_curr_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest,
        rendered_cells=missing_curr_cells,
        baseline_markdown=baseline_md,
        allowed_regressions={},
    )
    self.assertEqual(len(missing_curr_errors), 1)
    self.assertIn("current is ❓ (not_run)", missing_curr_errors[0])

    allowed_reg_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest,
        rendered_cells=rendered_cells,
        baseline_markdown=baseline_md,
        allowed_regressions={"basic/sec1": {"go/default"}},
    )
    self.assertEqual(allowed_reg_errors, [])

  def test_strict_manifest_catches_missing_column_and_not_run(self):
    manifest = [
        gcm.ManifestColumn(id="go", runtime="go", config="default", label="Go"),
        gcm.ManifestColumn(
            id="java", runtime="java", config="default", label="Java"
        ),
    ]
    report_with_not_run = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="HEAD",
        spec_commit="HEAD",
        configured_skips=(),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(gcm.TestResult("t1", "not_run"),),
                    ),
                ),
            ),
        ),
    )
    unrecognized_report = gcm.ConformanceReport(
        runtime="rust",
        config="default",
        runtime_version="HEAD",
        spec_commit="HEAD",
        configured_skips=(),
        suites=(),
    )
    errors = gcm.validate_manifest(
        manifest,
        {
            ("go", "default"): report_with_not_run,
            ("rust", "default"): unrecognized_report,
        },
    )
    self.assertEqual(len(errors), 3)
    self.assertIn("Unrecognized conformance report", errors[0])
    self.assertIn("has 'not_run' tests", errors[1])
    self.assertIn("Missing conformance report", errors[2])

  def test_validate_notes_checks_per_column_skip_coverage(self):
    manifest = [
        gcm.ManifestColumn(id="go", runtime="go", config="default", label="Go"),
        gcm.ManifestColumn(
            id="cpp", runtime="cpp", config="default", label="C++"
        ),
    ]
    rendered_cells = {
        ("basic", "sec1"): [
            gcm.CellStatus(
                category="skipped", rendered="⚠️ 1/2", passed=1, total=2
            ),
            gcm.CellStatus(category="na", rendered="`n/a`", passed=0, total=0),
        ]
    }
    column_notes = {("basic/sec1", "cpp"): "`C++`: Not applicable."}
    note_errors = gcm.validate_notes(
        rendered_cells,
        notes={},
        manifest=manifest,
        column_notes=column_notes,
    )
    self.assertEqual(len(note_errors), 1)
    self.assertIn("column 'Go'", note_errors[0])

    # Adding a column note for go satisfies validation.
    column_notes[("basic/sec1", "go")] = "`Go`: Tracked in issue #1."
    self.assertEqual(
        gcm.validate_notes(
            rendered_cells,
            notes={},
            manifest=manifest,
            column_notes=column_notes,
        ),
        [],
    )
    self.assertEqual(
        gcm.lookup_note(
            notes={},
            suite_name="basic",
            section_name="sec1",
            column_notes=column_notes,
            manifest=manifest,
            cells=rendered_cells[("basic", "sec1")],
        ),
        "`Go`: Tracked in issue #1.; `C++`: Not applicable.",
    )

  def test_regressions_catch_partial_skip_degradation_and_wildcard_collapse(
      self,
  ):
    manifest = [
        gcm.ManifestColumn(id="go", runtime="go", config="default", label="Go"),
        gcm.ManifestColumn(
            id="cpp", runtime="cpp", config="default", label="C++"
        ),
    ]
    more_skipped_report = gcm.ConformanceReport(
        runtime="go",
        config="default",
        runtime_version="v0.27.0",
        spec_commit="fe86fd9",
        configured_skips=("basic/sec1/t1", "basic/sec1/t2"),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(
                            gcm.TestResult("t1", "skipped"),
                            gcm.TestResult("t2", "skipped"),
                        ),
                    ),
                ),
            ),
        ),
    )
    # Baseline markdown with basic/sec1 having cell ⚠️ 1/2.
    baseline_md_snippet = (
        "| Suite | Section | Go | Notes |\n"
        "|---|---|---|---|\n"
        "| [`basic`](#) | [`sec1`](#) | ⚠️ 1/2 | |\n"
    )
    _, _, more_skipped_cells = gcm.render_markdown(
        manifest=manifest[:1],
        reports={("go", "default"): more_skipped_report},
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    reg_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest[:1],
        rendered_cells=more_skipped_cells,
        baseline_markdown=baseline_md_snippet,
        allowed_regressions={},
    )
    self.assertEqual(len(reg_errors), 1)
    self.assertIn(
        "baseline was ⚠️ 1/2 (skipped), current is ⚠️ 0/2 (skipped)",
        reg_errors[0],
    )
    # New skipped test added when total grows (⚠️ 1/2 -> ⚠️ 1/3) regresses.
    reg_errors_degrade = gcm.validate_no_regressions_from_markdown(
        manifest=manifest[:1],
        rendered_cells={
            ("basic", "sec1"): [
                gcm.CellStatus(
                    category="skipped", rendered="⚠️ 1/3", passed=1, total=3
                )
            ]
        },
        baseline_markdown=baseline_md_snippet,
        allowed_regressions={},
    )
    self.assertEqual(len(reg_errors_degrade), 1)
    self.assertIn(
        "baseline was ⚠️ 1/2 (skipped), current is ⚠️ 1/3 (skipped)",
        reg_errors_degrade[0],
    )
    # Removing a passing test from a skipped section without adding new skips
    # (⚠️ 1/2 -> ⚠️ 0/1) is NOT a regression.
    reg_errors_prune = gcm.validate_no_regressions_from_markdown(
        manifest=manifest[:1],
        rendered_cells={
            ("basic", "sec1"): [
                gcm.CellStatus(
                    category="skipped", rendered="⚠️ 0/1", passed=0, total=1
                )
            ]
        },
        baseline_markdown=baseline_md_snippet,
        allowed_regressions={},
    )
    self.assertEqual(reg_errors_prune, [])

    # Baseline Markdown with only C++ column, then Go inserted before C++ in
    # manifest and basic suite collapsed to (basic, "*") not_enabled.
    cpp_pass = gcm.ConformanceReport(
        runtime="cpp",
        config="default",
        runtime_version="HEAD",
        spec_commit="fe86fd9",
        configured_skips=(),
        suites=(
            gcm.SuiteReport(
                name="basic",
                file="basic.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="sec1",
                        tests=(gcm.TestResult("t1", "pass"),),
                    ),
                ),
            ),
        ),
    )
    baseline_md, _, _ = gcm.render_markdown(
        manifest=[manifest[1]],
        reports={("cpp", "default"): cpp_pass},
        notes_config=gcm.NotesConfig(
            notes={}, allowed_regressions={}, callouts=[]
        ),
    )
    collapsed_cells = {
        ("basic", "*"): [
            gcm.CellStatus(
                category="not_enabled", rendered="⬜", passed=0, total=0
            ),
            gcm.CellStatus(
                category="not_enabled", rendered="⬜", passed=0, total=0
            ),
        ]
    }
    md_reg_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest,
        rendered_cells=collapsed_cells,
        baseline_markdown=baseline_md,
        allowed_regressions={},
    )
    self.assertEqual(len(md_reg_errors), 1)
    self.assertIn("[cpp/default]", md_reg_errors[0])

    # Completely missing row in rendered_cells is also caught as a regression.
    missing_row_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest,
        rendered_cells={},
        baseline_markdown=baseline_md,
        allowed_regressions={},
    )
    self.assertEqual(len(missing_row_errors), 1)
    self.assertIn("current is `n/a` (na)", missing_row_errors[0])

  def test_validate_no_regressions_skipped_cell_transitions_and_allowlist(self):
    manifest = [
        gcm.ManifestColumn(
            id="go_default", runtime="go", config="default", label="Go"
        ),
    ]
    base_md = (
        "| Suite | Section | Go | Notes |\n"
        "|---|---|---|---|\n"
        "| [`basic`](#) | [`sec1`](#) | ⚠️ 1/2 | |\n"
    )

    # 1. Transitions from skipped to fail, not_enabled, not_run, and na regress.
    regressing_categories = [
        gcm.CellStatus("fail", "❌ 1/2", 1, 2),
        gcm.CellStatus("not_enabled", "⬜", 0, 2),
        gcm.CellStatus("not_run", "❓", 0, 2),
        gcm.CellStatus("na", "`n/a`", 0, 0),
    ]
    for target_cell in regressing_categories:
      errors = gcm.validate_no_regressions_from_markdown(
          manifest=manifest,
          rendered_cells={("basic", "sec1"): [target_cell]},
          baseline_markdown=base_md,
          allowed_regressions={},
      )
      self.assertEqual(
          len(errors),
          1,
          msg=f"Expected regression for transition to {target_cell.category}",
      )
      self.assertIn("Regression in 'basic/sec1'", errors[0])

    # 2. Non-regressing transition from skipped to pass.
    pass_cell = gcm.CellStatus("pass", "✅ 2/2", 2, 2)
    no_errors = gcm.validate_no_regressions_from_markdown(
        manifest=manifest,
        rendered_cells={("basic", "sec1"): [pass_cell]},
        baseline_markdown=base_md,
        allowed_regressions={},
    )
    self.assertEqual(no_errors, [])

    # 3. allowed_regressions matching via "*", wildcard row, runtime, col.id.
    fail_cell = gcm.CellStatus("fail", "❌ 1/2", 1, 2)
    for allow_config in [
        {"basic/sec1": {"*"}},
        {"basic/*": {"go_default"}},
        {"basic/sec1": {"go"}},
        {"basic/sec1": {"go_default"}},
    ]:
      errors = gcm.validate_no_regressions_from_markdown(
          manifest=manifest,
          rendered_cells={("basic", "sec1"): [fail_cell]},
          baseline_markdown=base_md,
          allowed_regressions=allow_config,
      )
      self.assertEqual(
          errors,
          [],
          msg=f"Expected allow_config={allow_config} to suppress regression",
      )

  def test_parse_baseline_markdown_special_cells(self):
    manifest = [
        gcm.ManifestColumn(
            id="go_default", runtime="go", config="default", label="Go"
        ),
        gcm.ManifestColumn(
            id="cpp_default", runtime="cpp", config="default", label="C++"
        ),
        gcm.ManifestColumn(
            id="java_default", runtime="java", config="default", label="Java"
        ),
    ]
    md = (
        "| Suite | Section | Go | C++ | Java | Notes |\n"
        "|---|---|---|---|---|---|\n"
        "| [`basic`](#) | [`sec1`](#) | `n/a` | ❓ | ⬜ (✅ 2/2) | |\n"
    )
    rows = gcm.parse_baseline_markdown(md, manifest)
    self.assertIn(("basic", "sec1"), rows)
    cells = rows[("basic", "sec1")]
    self.assertEqual(len(cells), 3)

    self.assertEqual(cells[0].category, "na")
    self.assertEqual(cells[0].rendered, "`n/a`")

    self.assertEqual(cells[1].category, "not_run")
    self.assertEqual(cells[1].rendered, "❓")

    self.assertEqual(cells[2].category, "not_enabled")
    self.assertEqual(cells[2].rendered, "⬜ (✅ 2/2)")
    self.assertEqual(cells[2].passed, 2)
    self.assertEqual(cells[2].total, 2)

  def _create_minimal_matrix_setup(self, tmpdir: str) -> dict[str, str]:
    manifest_path = os.path.join(tmpdir, "manifest.yaml")
    notes_path = os.path.join(tmpdir, "notes.yaml")
    report_path = os.path.join(tmpdir, "report.json")
    output_path = os.path.join(tmpdir, "conformance.md")
    testdata_dir = os.path.join(tmpdir, "testdata")
    os.makedirs(testdata_dir, exist_ok=True)

    with open(manifest_path, "w", encoding="utf-8") as f:
      f.write(
          "columns:\n"
          "  - id: go\n"
          "    runtime: go\n"
          "    config: default\n"
          "    label: Go\n"
      )
    with open(notes_path, "w", encoding="utf-8") as f:
      f.write("notes: {}\n")
    with open(
        os.path.join(testdata_dir, "basic.textproto"), "w", encoding="utf-8"
    ) as f:
      f.write(
          'name: "basic"\n'
          'section {\n  name: "sec1"\n  test {\n    name: "t1"\n  }\n}\n'
      )
    with open(report_path, "w", encoding="utf-8") as f:
      json.dump(
          {
              "runtime": "go",
              "config": "default",
              "suites": [{
                  "name": "basic",
                  "file": "basic.textproto",
                  "enabled": True,
                  "sections": [{
                      "name": "sec1",
                      "tests": [{"name": "t1", "status": "pass"}],
                  }],
              }],
          },
          f,
      )
    return {
        "manifest": manifest_path,
        "notes": notes_path,
        "report": report_path,
        "output": output_path,
        "testdata_dir": testdata_dir,
    }

  def test_main_fails_without_reports_or_testdata_dir(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
          ]),
          1,
      )

  def test_main_synthesizes_from_testdata_dir(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--testdata_dir",
              paths["testdata_dir"],
              "--strict_manifest",
              "--output",
              paths["output"],
          ]),
          0,
      )
      self.assertTrue(os.path.exists(paths["output"]))

  def test_main_generates_and_checks_consistency_from_reports(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              paths["report"],
              "--output",
              paths["output"],
          ]),
          0,
      )
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              paths["report"],
              "--baseline_markdown",
              paths["output"],
              "--output",
              paths["output"],
          ]),
          0,
      )
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              paths["report"],
              "--check_consistency",
              paths["output"],
          ]),
          0,
      )

  def test_main_writes_to_stdout_when_no_output_flag(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      stdout_buf = io.StringIO()
      with contextlib.redirect_stdout(stdout_buf):
        self.assertEqual(
            gcm.main([
                "--manifest",
                paths["manifest"],
                "--notes",
                paths["notes"],
                "--reports",
                paths["report"],
            ]),
            0,
        )
      self.assertIn("`basic`", stdout_buf.getvalue())
      self.assertIn("`sec1`", stdout_buf.getvalue())

  def test_main_detects_regression_when_baseline_and_output_share_path(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      # Generate initial baseline file.
      gcm.main([
          "--manifest",
          paths["manifest"],
          "--notes",
          paths["notes"],
          "--reports",
          paths["report"],
          "--output",
          paths["output"],
      ])
      regressed_report_path = os.path.join(tmpdir, "regressed_report.json")
      with open(regressed_report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "runtime": "go",
                "config": "default",
                "configured_skips": ["basic/sec1/stale_skip"],
                "suites": [{
                    "name": "basic",
                    "file": "basic.textproto",
                    "enabled": True,
                    "sections": [{
                        "name": "sec1",
                        "tests": [{"name": "t1", "status": "skipped"}],
                    }],
                }],
            },
            f,
        )
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              regressed_report_path,
              "--no_validate_skips",
              "--no_validate_notes",
              "--baseline_markdown",
              paths["output"],
              "--output",
              paths["output"],
          ]),
          1,
      )

  def test_main_skip_and_note_validation_flags(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      regressed_report_path = os.path.join(tmpdir, "regressed_report.json")
      with open(regressed_report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "runtime": "go",
                "config": "default",
                "configured_skips": ["basic/sec1/stale_skip"],
                "suites": [{
                    "name": "basic",
                    "file": "basic.textproto",
                    "enabled": True,
                    "sections": [{
                        "name": "sec1",
                        "tests": [{"name": "t1", "status": "skipped"}],
                    }],
                }],
            },
            f,
        )
      # Fails by default due to stale skip and missing note.
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              regressed_report_path,
          ]),
          1,
      )
      # Passes when flags disable those validation gates.
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              regressed_report_path,
              "--no_validate_skips",
              "--no_validate_notes",
              "--output",
              paths["output"],
          ]),
          0,
      )

  def test_main_fails_on_consistency_mismatch_or_invalid_notes(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      paths = self._create_minimal_matrix_setup(tmpdir)
      stale_path = os.path.join(tmpdir, "stale.md")
      with open(stale_path, "w", encoding="utf-8") as f:
        f.write("# Outdated matrix\n")
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              paths["notes"],
              "--reports",
              paths["report"],
              "--check_consistency",
              stale_path,
          ]),
          1,
      )
      bad_notes_path = os.path.join(tmpdir, "bad_notes.yaml")
      with open(bad_notes_path, "w", encoding="utf-8") as f:
        f.write("notes:\n  basic/sec1: 'Internal bug b/12345'\n")
      self.assertEqual(
          gcm.main([
              "--manifest",
              paths["manifest"],
              "--notes",
              bad_notes_path,
              "--reports",
              paths["report"],
          ]),
          1,
      )

  def test_committed_conformance_markdown_consistency(self):
    spec_root = _find_spec_root_dir()
    tools_dir = os.path.join(spec_root, "tools")
    manifest_path = os.path.join(tools_dir, "conformance_manifest.yaml")
    notes_path = os.path.join(tools_dir, "conformance_notes.yaml")
    testdata_dir = os.path.join(spec_root, "tests/simple/testdata")
    markdown_path = os.path.join(spec_root, "g3doc/conformance.md")
    if not os.path.exists(markdown_path):
      markdown_path = os.path.join(spec_root, "doc/conformance.md")

    exit_code = gcm.main([
        "--manifest",
        manifest_path,
        "--notes",
        notes_path,
        "--testdata_dir",
        testdata_dir,
        "--strict_manifest",
        "--baseline_markdown",
        markdown_path,
        "--check_consistency",
        markdown_path,
    ])
    self.assertEqual(exit_code, 0)

  def test_roundtrip_json_serialization(self):
    report = gcm.ConformanceReport(
        runtime="cpp",
        config="modern",
        runtime_version="v0.15.0",
        spec_commit="fe86fd9",
        configured_skips=("enums/strong_proto2",),
        skip_reasons={
            "enums/strong_proto2": "Strong enum typing unimplemented"
        },
        suites=(
            gcm.SuiteReport(
                name="type_deductions",
                file="type_deduction.textproto",
                enabled=True,
                sections=(
                    gcm.SectionReport(
                        name="constant_literals",
                        tests=(
                            gcm.TestResult(
                                "int",
                                "skipped",
                                reason="Strong enum typing",
                                observed="pass",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    with tempfile.TemporaryDirectory() as tmpdir:
      p1 = os.path.join(tmpdir, "r1.json")
      p2 = os.path.join(tmpdir, "r2.json")
      with open(p1, "w", encoding="utf-8") as f:
        f.write(json.dumps(gcm.report_to_dict(report)))
      loaded = gcm.load_report(p1)
      self.assertEqual(loaded, report)

      loaded_map = gcm.load_reports([p1])
      self.assertEqual(loaded_map, {("cpp", "modern"): report})

      with open(p2, "w", encoding="utf-8") as f:
        f.write(json.dumps(gcm.report_to_dict(report)))
      with self.assertRaisesRegex(ValueError, "Duplicate conformance report"):
        gcm.load_reports([p1, p2])

    with self.assertRaisesRegex(ValueError, "Invalid test status"):
      gcm.report_from_dict({
          "runtime": "go",
          "suites": [{
              "name": "basic",
              "sections": [{
                  "name": "s1",
                  "tests": [{"name": "t1", "status": "bogus_status"}],
              }],
          }],
      })

  def test_extract_build_skip_list_with_references_and_concatenation(self):
    build_content = """
_BASE_SKIPS = [
    "suite/sec1/t1",
    "suite/sec1/t2",
]
_MORE_SKIPS = [
    "suite/sec2/t3",
]
_COMBINED_SKIPS = _BASE_SKIPS + _MORE_SKIPS
_EXTENDED_SKIPS = _BASE_SKIPS + [
    "suite/sec3/t4",
]
_AUG_LIST = ["suite/sec4/t5"]
_AUG_LIST += ["suite/sec4/t6"]
"""
    with tempfile.TemporaryDirectory() as tmpdir:
      build_path = os.path.join(tmpdir, "BUILD")
      with open(build_path, "w", encoding="utf-8") as f:
        f.write(build_content)

      self.assertEqual(
          gcm.extract_build_skip_list(build_path, "_BASE_SKIPS"),
          ["suite/sec1/t1", "suite/sec1/t2"],
      )
      self.assertEqual(
          gcm.extract_build_skip_list(build_path, "_COMBINED_SKIPS"),
          ["suite/sec1/t1", "suite/sec1/t2", "suite/sec2/t3"],
      )
      self.assertEqual(
          gcm.extract_build_skip_list(build_path, "_EXTENDED_SKIPS"),
          ["suite/sec1/t1", "suite/sec1/t2", "suite/sec3/t4"],
      )
      self.assertEqual(
          gcm.extract_build_skip_list(build_path, "_AUG_LIST"),
          ["suite/sec4/t5", "suite/sec4/t6"],
      )
      with self.assertRaisesRegex(ValueError, "not found"):
        gcm.extract_build_skip_list(build_path, "_NONEXISTENT")

  def test_iter_path_prefixes_and_is_test_path_skipped(self):
    self.assertEqual(
        list(gcm._iter_path_prefixes("suite/sec/test")),
        ["suite", "suite/sec", "suite/sec/test"],
    )
    self.assertEqual(
        list(gcm._iter_path_prefixes("suite")),
        ["suite"],
    )
    skip_set = {"suite/sec", "other_suite"}
    self.assertTrue(gcm._is_test_path_skipped("suite/sec/test", skip_set))
    self.assertTrue(gcm._is_test_path_skipped("suite/sec", skip_set))
    self.assertTrue(gcm._is_test_path_skipped("other_suite", skip_set))
    self.assertFalse(
        gcm._is_test_path_skipped("suite/other_sec/test", skip_set)
    )
    self.assertFalse(gcm._is_test_path_skipped("suite", skip_set))

  def test_sync_manifest_from_build_files(self):
    manifest_yaml = """
columns:
  - id: go
    runtime: go
    config: default
    label: "Go"
    build_file: "go/BUILD"
    build_skip_var: "_GO_SKIPS"
    build_tests_var: "_ALL_TESTS"
    absent_suites:
      - absent_suite.textproto
    disabled_suites:
      - existing_disabled.textproto
    ignored_build_skips:
      - suite/sec_ignored/t_ignored
    absent_sections:
      - suite/sec_absent
    configured_skips:
      - suite/sec1/t_existing
"""
    go_build = """
_ALL_TESTS = [
    "//tests:testdata/suite.textproto",
]
_GO_SKIPS = [
    # Already configured:
    "suite/sec1/t_existing",
    # Ignored:
    "suite/sec_ignored/t_ignored",
    # Absent section:
    "suite/sec_absent/t1",
    # Not in testdata corpus:
    "suite/nonexistent_sec/t1",
    # New valid skip:
    "suite/sec2/t_new",
    # Skips in absent or disabled suites:
    "missing_suite/sec/t1",
    "existing_disabled/sec/t2",
    "absent_suite/sec/t3",
]
"""
    corpus: list[gcm.SuiteCorpus] = [
        (
            "suite",
            "suite.textproto",
            [
                ("sec1", ["t_existing"]),
                ("sec2", ["t_new"]),
                ("sec_ignored", ["t_ignored"]),
                ("sec_absent", ["t1"]),
            ],
        ),
        (
            "missing_suite",
            "missing_suite.textproto",
            [("sec", ["t1"])],
        ),
        (
            "existing_disabled",
            "existing_disabled.textproto",
            [("sec", ["t2"])],
        ),
        (
            "absent_suite",
            "absent_suite.textproto",
            [("sec", ["t3"])],
        ),
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
      manifest_path = os.path.join(tmpdir, "manifest.yaml")
      with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(manifest_yaml)
      os.makedirs(os.path.join(tmpdir, "go"), exist_ok=True)
      with open(os.path.join(tmpdir, "go/BUILD"), "w", encoding="utf-8") as f:
        f.write(go_build)

      changed = gcm.sync_manifest_from_build_files(
          manifest_path, corpus, build_root=tmpdir
      )
      self.assertTrue(changed)

      # Second run is a no-op:
      self.assertFalse(
          gcm.sync_manifest_from_build_files(
              manifest_path, corpus, build_root=tmpdir
          )
      )

      reloaded = gcm.load_manifest(manifest_path)
      # Missing suite added to disabled_suites; absent_suite not re-added:
      self.assertEqual(
          reloaded[0].disabled_suites,
          ("existing_disabled.textproto", "missing_suite.textproto"),
      )
      # Skips belonging to disabled or absent suites are filtered out:
      self.assertEqual(
          reloaded[0].configured_skips,
          ("suite/sec1/t_existing", "suite/sec2/t_new"),
      )

  def test_fallback_column_note_synthesis_and_runtime_grouping(self):
    manifest = [
        gcm.ManifestColumn(
            id="cpp_modern",
            runtime="cpp",
            config="modern",
            label="C++ (Modern)",
            configured_skips=("type_deductions/type_decay",),
        ),
        gcm.ManifestColumn(
            id="cpp_legacy",
            runtime="cpp",
            config="legacy",
            label="C++ (Legacy)",
            configured_skips=("type_deductions/type_decay",),
        ),
        gcm.ManifestColumn(
            id="java",
            runtime="java",
            config="default",
            label="Java",
            configured_skips=("type_deductions/type_decay/t1,t2",),
        ),
    ]
    corpus: list[gcm.SuiteCorpus] = [(
        "type_deductions",
        "type_deduction.textproto",
        [("type_decay", ["t1", "t2", "t3"])],
    )]
    reports = gcm.synthesize_reports_from_manifest(manifest, corpus)
    notes_cfg = gcm.NotesConfig(notes={}, allowed_regressions={}, callouts=[])
    _, _, rendered_cells = gcm.render_markdown(
        manifest=manifest,
        reports=reports,
        notes_config=notes_cfg,
        testdata_suites=corpus,
        synthesized=True,
    )
    note = gcm.lookup_note(
        notes=notes_cfg.notes,
        suite_name="type_deductions",
        section_name="type_decay",
        column_notes=notes_cfg.column_notes,
        manifest=manifest,
        cells=rendered_cells[("type_deductions", "type_decay")],
        reports=reports,
    )
    # C++ (Modern) and C++ (Legacy) group into "C++ skips `type_decay`".
    # Java skips individual tests `t1`, `t2`.
    self.assertEqual(note, "C++ skips `type_decay`; Java skips `t1`, `t2`")

    # validate_notes accepts synthesized fallback notes when reports is passed.
    val_errors = gcm.validate_notes(
        rendered_cells=rendered_cells,
        notes=notes_cfg.notes,
        column_notes=notes_cfg.column_notes,
        manifest=manifest,
        reports=reports,
    )
    self.assertEqual(val_errors, [])

    # Ungrouped when only one config of a runtime skips.
    cells_one_cpp = [
        rendered_cells[("type_deductions", "type_decay")][0],  # skipped
        gcm.CellStatus("pass", "✅ 3/3", 3, 3),  # pass
        rendered_cells[("type_deductions", "type_decay")][2],  # skipped
    ]
    note_one_cpp = gcm.lookup_note(
        notes=notes_cfg.notes,
        suite_name="type_deductions",
        section_name="type_decay",
        manifest=manifest,
        cells=cells_one_cpp,
        reports=reports,
    )
    self.assertEqual(
        note_one_cpp,
        "C++ (Modern) skips `type_decay`; Java skips `t1`, `t2`",
    )

  def test_insert_manifest_skips_edge_cases(self):
    manifest_yaml = """columns:
  - id: col_followed_by_key
    runtime: go
    config: default
    label: "Go"
    configured_skips:
      - suite/sec/existing_skip
    ignored_build_skips:
      - suite/sec/ignored_skip
  - id: col_empty_bracketed
    runtime: cpp
    config: modern
    label: "C++"
    configured_skips: []
    absent_sections:
      - suite/absent_sec
  - id: col_missing_skips_key
    runtime: java
    config: default
    label: "Java"
    runtime_version: "v1.0"
"""
    updated = gcm._insert_manifest_skips(
        manifest_yaml,
        {
            "col_followed_by_key": ["suite/sec/new_skip_1"],
            "col_empty_bracketed": ["suite/sec/new_skip_2"],
            "col_missing_skips_key": ["suite/sec/new_skip_3"],
        },
    )
    self.assertIn(
        "    configured_skips:\n"
        "      - suite/sec/existing_skip\n"
        "      - suite/sec/new_skip_1\n"
        "    ignored_build_skips:\n"
        "      - suite/sec/ignored_skip",
        updated,
    )
    self.assertIn(
        "    configured_skips:\n"
        "      - suite/sec/new_skip_2\n"
        "    absent_sections:\n"
        "      - suite/absent_sec",
        updated,
    )
    self.assertIn(
        "  - id: col_missing_skips_key\n"
        "    runtime: java\n"
        "    config: default\n"
        '    label: "Java"\n'
        '    runtime_version: "v1.0"\n'
        "    configured_skips:\n"
        "      - suite/sec/new_skip_3\n",
        updated,
    )

  def test_resolve_workspace_args_defaults_and_relative_paths(self):
    with tempfile.TemporaryDirectory() as tmpdir:
      spec_dir = os.path.join(tmpdir, "third_party/cel/spec")
      os.makedirs(os.path.join(spec_dir, "tools"), exist_ok=True)
      os.makedirs(
          os.path.join(spec_dir, "tests/simple/testdata"), exist_ok=True
      )
      os.makedirs(os.path.join(spec_dir, "g3doc"), exist_ok=True)
      manifest_file = os.path.join(spec_dir, "tools/conformance_manifest.yaml")
      with open(manifest_file, "w", encoding="utf-8") as f:
        f.write("columns: []\n")

      orig_ws = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
      try:
        os.environ["BUILD_WORKSPACE_DIRECTORY"] = tmpdir

        args_zero = argparse.Namespace(
            manifest=None,
            notes=None,
            testdata_dir=None,
            reports=[],
            output=None,
            check_consistency=None,
            baseline_markdown=None,
            build_root=None,
        )
        resolved_root = gcm._resolve_workspace_args(args_zero)
        self.assertEqual(resolved_root, tmpdir)
        self.assertEqual(
            args_zero.manifest,
            os.path.join(spec_dir, "tools/conformance_manifest.yaml"),
        )
        self.assertEqual(
            args_zero.notes,
            os.path.join(spec_dir, "tools/conformance_notes.yaml"),
        )
        self.assertEqual(
            args_zero.testdata_dir,
            os.path.join(spec_dir, "tests/simple/testdata"),
        )
        self.assertEqual(
            args_zero.output,
            os.path.join(spec_dir, "g3doc/conformance.md"),
        )

        args_partial = argparse.Namespace(
            manifest=None,
            notes=None,
            testdata_dir=None,
            reports=[],
            output=None,
            check_consistency="g3doc/conformance.md",
            baseline_markdown=None,
            build_root="/custom/build/root",
        )
        resolved_custom = gcm._resolve_workspace_args(args_partial)
        self.assertEqual(resolved_custom, "/custom/build/root")
        self.assertIsNone(args_partial.output)
        self.assertEqual(
            args_partial.manifest,
            os.path.join(spec_dir, "tools/conformance_manifest.yaml"),
        )
        self.assertEqual(
            args_partial.check_consistency,
            os.path.join(spec_dir, "g3doc/conformance.md"),
        )
      finally:
        if orig_ws is not None:
          os.environ["BUILD_WORKSPACE_DIRECTORY"] = orig_ws
        else:
          os.environ.pop("BUILD_WORKSPACE_DIRECTORY", None)

  def test_collect_corpus_union_absent_sections_vs_excluded_sections(self):
    manifest = [
        gcm.ManifestColumn(
            id="go_default",
            runtime="go",
            config="default",
            label="Go",
            absent_sections=("suite/sec_absent", "suite/sec_excluded"),
            excluded_sections=("suite/sec_excluded",),
        ),
        gcm.ManifestColumn(
            id="cpp_modern",
            runtime="cpp",
            config="modern",
            label="C++",
            absent_sections=("suite/sec_absent", "suite/sec_excluded"),
            excluded_sections=("suite/sec_excluded",),
        ),
    ]
    testdata_suites: list[gcm.SuiteCorpus] = [(
        "suite",
        "suite.textproto",
        [
            ("sec_active", ["test1"]),
            ("sec_absent", ["test2"]),
            ("sec_excluded", ["test3"]),
        ],
    )]
    _, suite_sections = gcm.collect_corpus_union(
        manifest, reports={}, testdata_suites=testdata_suites
    )
    self.assertIn("sec_active", suite_sections["suite"])
    self.assertIn("sec_absent", suite_sections["suite"])
    self.assertNotIn("sec_excluded", suite_sections["suite"])


if __name__ == "__main__":
  absltest.main()
