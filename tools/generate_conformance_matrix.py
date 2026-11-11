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
"""Generates and validates the CEL Conformance Coverage Matrix."""

import argparse
import ast
from collections.abc import Iterable, Iterator, Mapping, Sequence, Set
import dataclasses
import difflib
import itertools
import json
import os
import re
import sys
import textwrap
import types
from typing import Any

from cel.expr.conformance.proto2 import test_all_types_extensions_pb2 as _ext
from cel.expr.conformance.proto2 import test_all_types_pb2 as _proto2
from cel.expr.conformance.proto3 import test_all_types_pb2 as _proto3
from cel.expr.conformance.test import simple_pb2
from google.protobuf import text_format
import yaml

# Register extension and Any message descriptors in the default pool.
_ = (_ext.DESCRIPTOR, _proto2.DESCRIPTOR, _proto3.DESCRIPTOR)

GITHUB_TESTDATA_BASE_URL = (
    "https://github.com/cel-expr/cel-conformance/blob/main/"
    "tests/simple/testdata"
)

VALID_TEST_STATUSES = frozenset(
    {"pass", "fail", "skipped", "not_enabled", "not_run"}
)

_RUNTIME_LABELS = types.MappingProxyType(
    {"go": "Go", "cpp": "C++", "java": "Java"}
)
_KNOWN_FILENAME_TO_SUITE = types.MappingProxyType(
    {"type_deduction.textproto": "type_deductions"}
)
_KNOWN_SUITE_TO_FILENAME = types.MappingProxyType({
    suite_name: filename
    for filename, suite_name in _KNOWN_FILENAME_TO_SUITE.items()
})
_VALID_CALLOUT_KEYS = frozenset({"title", "runtimes", "tracking", "details"})
_NEXT_COL_RE = re.compile(r"^  - id:\s*")
_ANY_COL_RE = re.compile(r"^\s*- id:\s*")
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")

# A section name and its ordered test names from a .textproto suite file.
SectionCorpus = tuple[str, Sequence[str]]
# A (suite_name, filename, sections) tuple representing a parsed suite file.
SuiteCorpus = tuple[str, str, Sequence[SectionCorpus]]


@dataclasses.dataclass(frozen=True, slots=True)
class ManifestColumn:
  """Represents an expected (runtime, config) column in the matrix.

  Attributes:
    id: Unique column identifier (e.g. 'go_default').
    runtime: Runtime name (e.g. 'go', 'cpp', 'java').
    config: Configuration variant name (e.g. 'default', 'modern', 'legacy').
    label: Human-readable column header label rendered in Markdown.
    disabled_suites: Suite filenames (.textproto) not enabled for this column.
    configured_skips: Skip prefix patterns configured for this column.
    runtime_version: Release tag or commit rendered in the Runtime Version row.
    spec_commit: Pinned cel-spec commit rendered in the Spec Commit row.
    absent_suites: Suite filenames absent at the column's pinned spec commit.
    absent_sections: 'suite/section' keys absent or excluded for this column.
    absent_tests: 'suite/section/test' keys absent or excluded for this column.
    excluded_sections: 'suite/section' keys excluded globally from the matrix.
    build_file: Relative workspace path to runtime BUILD file with skips.
    build_skip_var: Variable name in build_file defining the skip list.
    build_tests_var: Variable name in build_file defining the test suites list.
    ignored_build_skips: Build skips ignored during manifest synchronization.
    ignored_disabled_suites: Suite filenames omitted from build_tests_var that
      should not be synced into disabled_suites.
  """

  id: str
  runtime: str
  config: str
  label: str
  disabled_suites: tuple[str, ...] = ()
  configured_skips: tuple[str, ...] = ()
  # Release metadata rendered in the Runtime Version / Spec Commit rows when
  # reports are synthesized from the manifest.
  runtime_version: str = "HEAD"
  spec_commit: str = "HEAD"
  # Suite files / "suite/section" keys / "suite/section/test" keys that do not
  # exist at the column's pinned cel-spec version or are excluded from the
  # matrix.
  absent_suites: tuple[str, ...] = ()
  absent_sections: tuple[str, ...] = ()
  absent_tests: tuple[str, ...] = ()
  excluded_sections: tuple[str, ...] = ()
  build_file: str = ""
  build_skip_var: str = ""
  build_tests_var: str = ""
  ignored_build_skips: tuple[str, ...] = ()
  ignored_disabled_suites: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class TestResult:
  """Represents a single test result within a section.

  Attributes:
    name: Name of the individual test case.
    status: Execution status in VALID_TEST_STATUSES.
    reason: Optional explanation when status is 'skipped' or 'fail'.
    observed: Optional underlying outcome ('pass' or 'fail') in dashboard mode.
  """

  name: str
  status: str
  reason: str = ""
  observed: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class SectionReport:
  """Represents a section within a test suite.

  Attributes:
    name: Name of the test section within the suite.
    tests: Tuple of TestResult entries belonging to this section.
  """

  name: str
  tests: tuple[TestResult, ...]


@dataclasses.dataclass(frozen=True, slots=True)
class SuiteReport:
  """Represents a test suite (.textproto file) in a conformance report.

  Attributes:
    name: Logical suite name from the SimpleTestFile header.
    file: Basename of the .textproto file (e.g. 'basic.textproto').
    enabled: Whether the suite is enabled in the runtime's standard test target.
    sections: Tuple of SectionReport entries in this suite.
  """

  name: str
  file: str
  enabled: bool
  sections: tuple[SectionReport, ...]
  _section_map: dict[str, SectionReport] = dataclasses.field(
      init=False, repr=False, compare=False, default_factory=dict
  )

  def __post_init__(self) -> None:
    object.__setattr__(
        self, "_section_map", {sec.name: sec for sec in self.sections}
    )

  def get_section(self, section_name: str) -> SectionReport | None:
    """Returns the SectionReport for section_name, or None if not found.

    Args:
      section_name: Name of the test section to look up within this suite.

    Returns:
      The matching SectionReport if present in this suite, or None if no
      section with section_name exists.
    """
    return self._section_map.get(section_name)


@dataclasses.dataclass(frozen=True, slots=True)
class ConformanceReport:
  """Represents an intermediate conformance_report.json record.

  Attributes:
    runtime: Runtime identifier (e.g. 'go', 'cpp', 'java').
    config: Configuration identifier (e.g. 'default', 'modern', 'legacy').
    runtime_version: Version string or commit SHA of the runtime.
    spec_commit: Version string or commit SHA of cel-spec.
    configured_skips: Tuple of skip patterns configured by the test runner.
    suites: Tuple of SuiteReport records for each suite in the corpus.
    skip_reasons: Mapping from skip pattern to human-readable skip reason.
  """

  runtime: str
  config: str
  runtime_version: str
  spec_commit: str
  configured_skips: tuple[str, ...]
  suites: tuple[SuiteReport, ...]
  skip_reasons: dict[str, str] = dataclasses.field(default_factory=dict)
  _suite_map: dict[str, SuiteReport] = dataclasses.field(
      init=False, repr=False, compare=False, default_factory=dict
  )

  def __post_init__(self) -> None:
    object.__setattr__(self, "_suite_map", {s.name: s for s in self.suites})

  def get_suite(self, suite_name: str) -> SuiteReport | None:
    """Returns the SuiteReport for suite_name, or None if not found.

    Args:
      suite_name: Name of the test suite to look up within this report.

    Returns:
      The matching SuiteReport if present in this report, or None if no suite
      with suite_name exists.
    """
    return self._suite_map.get(suite_name)


@dataclasses.dataclass(frozen=True, slots=True)
class CellStatus:
  """Resolved status and rendered string for a single (row, column) cell.

  Attributes:
    category: Status category ('na', 'not_enabled', 'not_run', 'fail',
      'skipped', 'pass', or 'unknown').
    rendered: Formatted Markdown cell text (e.g. '✅ 12/12' or '⬜').
    passed: Number of passing tests in the section or suite.
    total: Total number of tests in the section or suite.
  """

  category: str  # "na", "not_enabled", "not_run", "fail", "skipped", "pass"
  rendered: str
  passed: int
  total: int


@dataclasses.dataclass(frozen=True, slots=True)
class Callout:
  """Represents a callout block rendered below the conformance matrix.

  Attributes:
    title: Required heading displayed at the top of the callout block.
    runtimes: Optional comma-separated summary of affected runtimes.
    tracking: Optional public tracking link or issue reference.
    details: Optional detailed explanation for the callout.
  """

  title: str
  runtimes: str = ""
  tracking: str = ""
  details: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class NotesConfig:
  """Parsed conformance_notes.yaml configuration.

  Attributes:
    notes: Mapping from 'suite/section' (or 'suite/*') to shared note text.
    allowed_regressions: Mapping from row key to set of column IDs (or {'*'})
      allowed to regress.
    callouts: Tuple of Callout blocks rendered below the conformance matrix.
    column_notes: Mapping from (row_key, column_key) to column-specific note.
  """

  notes: dict[str, str]
  allowed_regressions: dict[str, set[str]]
  callouts: tuple[Callout, ...] = ()
  column_notes: dict[tuple[str, str], str] = dataclasses.field(
      default_factory=dict
  )


def expand_skip_patterns(skip_entries: Sequence[str]) -> list[str]:
  """Expands comma-separated skip entries into individual prefix patterns.

  Args:
    skip_entries: Raw skip strings, optionally containing comma-separated leaf
      names after the last '/' (e.g. 'suite/sec/t1,t2').

  Returns:
    A list of individual normalized path-segment prefix patterns.
  """
  expanded: list[str] = []
  for raw_entry in skip_entries:
    entry = raw_entry.strip().strip('"').strip("'")
    if not entry:
      continue
    comma_idx = entry.find(",")
    if comma_idx == -1:
      expanded.append(entry)
      continue
    slash_idx = entry.rfind("/", 0, comma_idx)
    prefix = "" if slash_idx == -1 else entry[: slash_idx + 1]
    suffix = entry if slash_idx == -1 else entry[slash_idx + 1 :]
    for part in suffix.split(","):
      part = part.strip()
      if part:
        expanded.append(prefix + part)
  return expanded


def parse_simple_textproto_content(
    content: str, default_filename: str
) -> SuiteCorpus:
  """Parses a SimpleTestFile textproto into (suite_name, filename, sections).

  Args:
    content: Raw textproto content.
    default_filename: Basename of the .textproto file (e.g. 'basic.textproto').

  Returns:
    A tuple of (suite_name, filename, [(section_name, [test_names])]).
  """
  test_file = simple_pb2.SimpleTestFile()
  text_format.Parse(content, test_file)
  suite_name = test_file.name or os.path.splitext(default_filename)[0]
  sections_by_name: dict[str, list[str]] = {}
  for section in test_file.section:
    section_name = section.name or "unnamed"
    test_names = [test.name or "unnamed" for test in section.test]
    sections_by_name.setdefault(section_name, []).extend(test_names)
  return suite_name, default_filename, list(sections_by_name.items())


def load_testdata_corpus(
    testdata_dir: str,
) -> list[SuiteCorpus]:
  """Loads all .textproto files from testdata_dir sorted by suite name.

  Args:
    testdata_dir: Directory path containing SimpleTestFile .textproto files.

  Returns:
    A list of parsed (suite_name, filename, sections) tuples sorted by
    suite_name.
  """
  suites: list[SuiteCorpus] = []
  for entry in sorted(os.listdir(testdata_dir)):
    if not entry.endswith(".textproto"):
      continue
    filepath = os.path.join(testdata_dir, entry)
    with open(filepath, "r", encoding="utf-8") as f:
      content = f.read()
    suites.append(parse_simple_textproto_content(content, entry))
  suites.sort(key=lambda item: item[0])
  return suites


def load_manifest(path: str) -> list[ManifestColumn]:
  """Loads the expected-matrix manifest from a YAML file.

  Args:
    path: Filesystem path to conformance_manifest.yaml.

  Returns:
    Ordered list of ManifestColumn definitions.
  """
  with open(path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}
  excluded_sections = [str(s) for s in data.get("excluded_sections") or []]
  excluded_tests = [str(s) for s in data.get("excluded_tests") or []]
  columns: list[ManifestColumn] = []
  for item in data.get("columns", []):
    disabled = tuple(str(s) for s in item.get("disabled_suites") or [])
    skips = tuple(str(s) for s in item.get("configured_skips") or [])
    col_absent_sections = [
        str(s) for s in item.get("absent_sections") or []
    ] + excluded_sections
    col_absent_tests = [
        str(s) for s in item.get("absent_tests") or []
    ] + excluded_tests
    columns.append(
        ManifestColumn(
            id=str(item["id"]),
            runtime=str(item["runtime"]),
            config=str(item["config"]),
            label=str(item["label"]),
            disabled_suites=disabled,
            configured_skips=skips,
            runtime_version=str(item.get("runtime_version") or "HEAD"),
            spec_commit=str(item.get("spec_commit") or "HEAD"),
            absent_suites=tuple(
                str(s) for s in item.get("absent_suites") or []
            ),
            absent_sections=tuple(dict.fromkeys(col_absent_sections)),
            absent_tests=tuple(dict.fromkeys(col_absent_tests)),
            excluded_sections=tuple(dict.fromkeys(excluded_sections)),
            build_file=str(item.get("build_file") or ""),
            build_skip_var=str(item.get("build_skip_var") or ""),
            build_tests_var=str(item.get("build_tests_var") or ""),
            ignored_build_skips=tuple(
                str(s) for s in item.get("ignored_build_skips") or []
            ),
            ignored_disabled_suites=tuple(
                str(s) for s in item.get("ignored_disabled_suites") or []
            ),
        )
    )
  return columns


def extract_build_skip_list(build_path: str, var_name: str) -> list[str]:
  """Extracts a list of skip strings from a Bazel BUILD file via AST parsing.

  Args:
    build_path: Filesystem path to the BUILD file to parse.
    var_name: Name of the top-level list variable to extract.

  Returns:
    Evaluated list of string literals assigned to var_name.

  Raises:
    ValueError: If var_name is not defined at top level in build_path.
  """
  with open(build_path, "r", encoding="utf-8") as f:
    tree = ast.parse(f.read(), filename=build_path)

  env: dict[str, list[str]] = {}

  def _eval_list_expr(node: ast.AST) -> list[str]:
    """Evaluates a Starlark string-list AST expression using parsed bindings.

    Args:
      node: AST node representing a list literal, variable name, or `+` list
        concatenation expression.

    Returns:
      Evaluated list of string literals, or an empty list if node is an
      unsupported expression or references an unknown variable.
    """
    if isinstance(node, ast.List):
      return [
          elt.value
          for elt in node.elts
          if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
      ]
    if isinstance(node, ast.Name) and node.id in env:
      return list(env[node.id])
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
      return _eval_list_expr(node.left) + _eval_list_expr(node.right)
    return []

  for stmt in tree.body:
    if isinstance(stmt, ast.Assign):
      val = _eval_list_expr(stmt.value)
      for target in stmt.targets:
        if isinstance(target, ast.Name):
          env[target.id] = val
    elif isinstance(stmt, ast.AugAssign) and isinstance(stmt.op, ast.Add):
      if isinstance(stmt.target, ast.Name):
        env[stmt.target.id] = env.get(stmt.target.id, []) + _eval_list_expr(
            stmt.value
        )

  if var_name not in env:
    raise ValueError(f"Variable '{var_name}' not found in {build_path}")
  return env[var_name]


def _iter_path_prefixes(path: str) -> Iterator[str]:
  """Yields all slash-delimited prefixes of path, including path itself.

  Args:
    path: Slash-delimited test path (e.g. 'suite/section/test').

  Yields:
    Progressively longer slash-delimited prefixes of path, ending with path.
  """
  if not path:
    return
  yield from itertools.accumulate(path.split("/"), lambda a, b: f"{a}/{b}")


def _is_test_path_skipped(path: str, skip_set: Set[str]) -> bool:
  """Returns True if path or any slash-delimited prefix is in skip_set.

  Args:
    path: Full slash-delimited test path (e.g. 'suite/section/test').
    skip_set: Set of expanded skip patterns to match against path prefixes.

  Returns:
    True if path or any slash-delimited ancestor prefix appears in skip_set.
  """
  return any(prefix in skip_set for prefix in _iter_path_prefixes(path))


def _format_yaml_scalar(entry: str) -> str:
  """Formats a list entry as a single-line YAML scalar via PyYAML.

  Args:
    entry: Raw string item to emit as a YAML sequence element.

  Returns:
    Single-line YAML scalar representation of entry.
  """
  dumped = yaml.safe_dump(
      [entry],
      default_flow_style=False,
      allow_unicode=True,
      width=10000,
  )
  lines = dumped.splitlines()
  if len(lines) != 1 or not lines[0].startswith("- "):
    dumped = yaml.safe_dump(
        [entry],
        default_flow_style=False,
        allow_unicode=True,
        width=10000,
        default_style='"',
    )
    lines = dumped.splitlines()
  return lines[0][2:]


def _insert_manifest_skips(
    text: str,
    new_entries_for_col: Mapping[str, Sequence[str]],
    key_name: str = "configured_skips",
) -> str:
  """Inserts new entries into key_name for each column.

  Args:
    text: Raw YAML text of the conformance manifest.
    new_entries_for_col: Mapping from column ID to sequence of new entries to
      insert.
    key_name: Target list key name (e.g. 'configured_skips', 'disabled_suites').

  Returns:
    Updated YAML text with the new entries inserted.
  """
  lines = text.splitlines(keepends=True)
  insertions: list[tuple[int, list[str]]] = []
  target_key_re = re.compile(rf"^(\s*){re.escape(key_name)}:(.*)$")

  for col_id, new_entries in new_entries_for_col.items():
    if not new_entries:
      continue
    id_pattern = re.compile(rf"^(\s*)- id:\s*{re.escape(col_id)}\b")
    id_idx = None
    col_indent = 2
    for idx, line in enumerate(lines):
      id_match = id_pattern.match(line)
      if id_match:
        id_idx = idx
        col_indent = len(id_match.group(1))
        break
    if id_idx is None:
      continue

    next_id_idx = len(lines)
    for idx in range(id_idx + 1, len(lines)):
      line = lines[idx]
      if _NEXT_COL_RE.match(line) or (
          _ANY_COL_RE.match(line)
          and (len(line) - len(line.lstrip(" "))) <= col_indent
      ):
        next_id_idx = idx
        break

    header_idx = None
    for idx in range(id_idx + 1, next_id_idx):
      key_match = target_key_re.match(lines[idx])
      if key_match and len(key_match.group(1)) > col_indent:
        header_idx = idx
        break

    if header_idx is not None:
      key_indent = len(lines[header_idx]) - len(lines[header_idx].lstrip(" "))
      item_indent: int | None = None
      last_entry_idx = None
      for idx in range(header_idx + 1, next_id_idx):
        line = lines[idx]
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
          continue
        indent = len(line) - len(stripped)
        if indent >= key_indent and stripped.startswith("- "):
          if item_indent is None:
            item_indent = indent
          last_entry_idx = idx
        elif indent <= key_indent:
          break
      effective_item_indent = (
          item_indent if item_indent is not None else key_indent + 2
      )
      item_prefix = (" " * effective_item_indent) + "- "
      to_insert = [
          f"{item_prefix}{_format_yaml_scalar(entry)}\n"
          for entry in new_entries
      ]
      if last_entry_idx is not None:
        if not lines[last_entry_idx].endswith("\n"):
          lines[last_entry_idx] += "\n"
        insertions.append((last_entry_idx + 1, to_insert))
      else:
        lines[header_idx] = (" " * key_indent) + key_name + ":\n"
        insertions.append((header_idx + 1, to_insert))
    else:
      key_indent = col_indent + 2
      for idx in range(id_idx + 1, next_id_idx):
        line = lines[idx]
        stripped = line.lstrip(" ")
        if not stripped or stripped.startswith("#"):
          continue
        indent = len(line) - len(stripped)
        if indent > col_indent and not stripped.startswith("- "):
          key_indent = indent
          break
      insert_at = next_id_idx
      while insert_at > id_idx + 1:
        prev = lines[insert_at - 1]
        prev_stripped = prev.lstrip(" ")
        if not prev_stripped or (
            prev_stripped.startswith("#")
            and (len(prev) - len(prev_stripped)) <= col_indent
        ):
          insert_at -= 1
        else:
          break
      item_prefix = (" " * (key_indent + 2)) + "- "
      to_insert = [
          f"{item_prefix}{_format_yaml_scalar(entry)}\n"
          for entry in new_entries
      ]
      if insert_at > 0 and not lines[insert_at - 1].endswith("\n"):
        lines[insert_at - 1] += "\n"
      full_insert = [(" " * key_indent) + key_name + ":\n"] + to_insert
      insertions.append((insert_at, full_insert))

  # Apply insertions from bottom to top to preserve line indices.
  insertions.sort(key=lambda item: item[0], reverse=True)
  for insert_idx, to_insert in insertions:
    lines[insert_idx:insert_idx] = to_insert

  return "".join(lines)


def sync_manifest_from_build_files(
    manifest_path: str,
    testdata_suites: Sequence[SuiteCorpus],
    build_root: str,
) -> bool:
  """Syncs new valid skips and disabled suites from BUILD files into manifest.

  Args:
    manifest_path: Filesystem path to conformance_manifest.yaml.
    testdata_suites: Parsed testdata suites from the .textproto corpus.
    build_root: Workspace root directory where runtime BUILD files reside.

  Returns:
    True if the manifest was modified, False otherwise.

  Raises:
    ValueError: If a configured build_tests_var or build_skip_var is missing
      from its runtime BUILD file.
  """
  manifest = load_manifest(manifest_path)
  valid_prefixes = collect_corpus_prefixes(testdata_suites)
  new_entries_for_col: dict[str, list[str]] = {}
  new_disabled_for_col: dict[str, list[str]] = {}
  build_list_cache: dict[tuple[str, str], list[str]] = {}

  def _get_build_list(build_path: str, var_name: str) -> list[str]:
    """Fetches and caches an extracted string list from a BUILD file.

    Args:
      build_path: Filesystem path to the runtime BUILD file.
      var_name: Top-level Starlark list variable name to extract.

    Returns:
      Cached or freshly extracted list of strings assigned to var_name.

    Raises:
      ValueError: If var_name is not defined at top level in build_path.
    """
    cache_key = (build_path, var_name)
    if cache_key not in build_list_cache:
      build_list_cache[cache_key] = extract_build_skip_list(
          build_path, var_name
      )
    return build_list_cache[cache_key]

  for col in manifest:
    if not col.build_file or (
        not col.build_skip_var and not col.build_tests_var
    ):
      continue
    build_path = os.path.join(build_root, col.build_file)
    if not os.path.isfile(build_path):
      continue

    excluded_files = set(col.absent_suites) | set(col.disabled_suites)
    if col.build_tests_var:
      raw_tests = _get_build_list(build_path, col.build_tests_var)
      build_test_files = {
          os.path.basename(entry.strip().rsplit(":", 1)[-1])
          for entry in raw_tests
      }
      known_disabled = excluded_files | set(col.ignored_disabled_suites)
      col_new_disabled: list[str] = []
      seen_disabled: set[str] = set()
      for _, filename, _ in testdata_suites:
        if (
            filename not in build_test_files
            and filename not in known_disabled
            and filename not in seen_disabled
        ):
          seen_disabled.add(filename)
          col_new_disabled.append(filename)
      if col_new_disabled:
        new_disabled_for_col[col.id] = col_new_disabled
        excluded_files.update(col_new_disabled)

    disabled_suite_names = {
        suite_name
        for suite_name, filename, _ in testdata_suites
        if filename in excluded_files
    }

    if col.build_skip_var:
      raw_skips = _get_build_list(build_path, col.build_skip_var)
      covered_prefixes: set[str] = (
          set(expand_skip_patterns(col.configured_skips))
          | set(expand_skip_patterns(col.ignored_build_skips))
          | disabled_suite_names
          | set(col.absent_sections)
          | set(col.absent_tests)
      )
      col_new: list[str] = []
      for raw_entry in raw_skips:
        expanded = expand_skip_patterns([raw_entry])
        kept = [
            p
            for p in expanded
            if p in valid_prefixes
            and not _is_test_path_skipped(p, covered_prefixes)
        ]
        if kept:
          if len(kept) == len(expanded):
            col_new.append(raw_entry.strip().strip('"').strip("'"))
          else:
            col_new.extend(kept)
          covered_prefixes.update(kept)
      if col_new:
        new_entries_for_col[col.id] = col_new

  if not new_disabled_for_col and not new_entries_for_col:
    return False

  with open(manifest_path, "r", encoding="utf-8") as f:
    text = f.read()

  if new_disabled_for_col:
    text = _insert_manifest_skips(
        text, new_disabled_for_col, key_name="disabled_suites"
    )
  if new_entries_for_col:
    text = _insert_manifest_skips(
        text, new_entries_for_col, key_name="configured_skips"
    )

  with open(manifest_path, "w", encoding="utf-8") as f:
    f.write(text)

  return True


def load_notes(path: str) -> NotesConfig:
  """Loads side-car notes and callout blocks from conformance_notes.yaml.

  Args:
    path: Filesystem path to conformance_notes.yaml.

  Returns:
    Parsed NotesConfig containing row notes, column notes, allowed regressions,
    and callout blocks.

  Raises:
    ValueError: If any callout entry is not a mapping, contains unknown keys,
      or is missing a non-empty 'title'.
  """
  with open(path, "r", encoding="utf-8") as f:
    data = yaml.safe_load(f) or {}

  notes: dict[str, str] = {}
  column_notes: dict[tuple[str, str], str] = {}
  allowed_regressions: dict[str, set[str]] = {}
  raw_notes = data.get("notes", {}) or {}
  for key, val in raw_notes.items():
    key_str = str(key)
    if isinstance(val, str):
      notes[key_str] = val
    elif isinstance(val, Mapping):
      note_text = str(val.get("note", "")).strip()
      if note_text:
        notes[key_str] = note_text
      col_map = val.get("columns")
      if isinstance(col_map, Mapping):
        for col_key, col_note in col_map.items():
          col_note_str = str(col_note).strip()
          if col_note_str:
            column_notes[(key_str, str(col_key))] = col_note_str
      allow_reg = val.get("allow_regression")
      if isinstance(allow_reg, bool) and allow_reg:
        allowed_regressions[key_str] = {"*"}
      elif isinstance(allow_reg, Sequence) and not isinstance(allow_reg, str):
        allowed_regressions[key_str] = {str(x) for x in allow_reg}

  callouts: list[Callout] = []
  for idx, item in enumerate(data.get("callouts", []) or []):
    if not isinstance(item, Mapping):
      raise ValueError(
          f"Callout entry #{idx} in {path} must be a mapping, got"
          f" {type(item).__name__}."
      )
    unknown = {str(k) for k in item.keys()} - _VALID_CALLOUT_KEYS
    if unknown:
      raise ValueError(
          f"Callout entry #{idx} in {path} has unknown key(s):"
          f" {sorted(unknown)}."
      )
    title = str(item.get("title") or "").strip()
    if not title:
      raise ValueError(
          f"Callout entry #{idx} in {path} must define a non-empty 'title'."
      )
    callouts.append(
        Callout(
            title=title,
            runtimes=str(item.get("runtimes") or "").strip(),
            tracking=str(item.get("tracking") or "").strip(),
            details=str(item.get("details") or "").strip(),
        )
    )

  return NotesConfig(
      notes=notes,
      allowed_regressions=allowed_regressions,
      callouts=tuple(callouts),
      column_notes=column_notes,
  )


def report_from_dict(data: Mapping[str, Any]) -> ConformanceReport:
  """Parses a ConformanceReport from a JSON dictionary.

  Args:
    data: Decoded JSON mapping representing a conformance report.

  Returns:
    Parsed ConformanceReport instance.

  Raises:
    ValueError: If any test entry has a status not in VALID_TEST_STATUSES.
  """
  suites: list[SuiteReport] = []
  for suite_data in data.get("suites", []):
    suite_name = str(suite_data["name"])
    suite_file = str(
        suite_data.get("file")
        or _KNOWN_SUITE_TO_FILENAME.get(suite_name, f"{suite_name}.textproto")
    )
    enabled = bool(suite_data.get("enabled", True))
    sections: list[SectionReport] = []
    for section_data in suite_data.get("sections", []):
      tests: list[TestResult] = []
      for test_data in section_data.get("tests", []):
        status = str(test_data["status"])
        if status not in VALID_TEST_STATUSES:
          sec_name = section_data.get("name")
          t_name = test_data.get("name")
          raise ValueError(
              f"Invalid test status {status!r} in"
              f" {suite_name}/{sec_name}/{t_name}"
          )
        tests.append(
            TestResult(
                name=str(test_data["name"]),
                status=status,
                reason=str(test_data.get("reason", "")),
                observed=str(test_data.get("observed", "")),
            )
        )
      sections.append(
          SectionReport(name=str(section_data["name"]), tests=tuple(tests))
      )
    suites.append(
        SuiteReport(
            name=suite_name,
            file=suite_file,
            enabled=enabled,
            sections=tuple(sections),
        )
    )

  raw_skip_reasons = data.get("skip_reasons", {}) or {}
  skip_reasons = {str(k): str(v) for k, v in raw_skip_reasons.items()}

  return ConformanceReport(
      runtime=str(data["runtime"]),
      config=str(data.get("config", "default")),
      runtime_version=str(data.get("runtime_version", "HEAD")),
      spec_commit=str(data.get("spec_commit", "HEAD")),
      configured_skips=tuple(str(s) for s in data.get("configured_skips", [])),
      suites=tuple(suites),
      skip_reasons=skip_reasons,
  )


def _test_to_dict(test: TestResult) -> dict[str, str]:
  """Serializes a TestResult to a minimal JSON-compatible dictionary.

  Args:
    test: TestResult record to serialize.

  Returns:
    Dictionary containing 'name', 'status', and optional 'reason' and 'observed'
    fields when non-empty.
  """
  result: dict[str, str] = {"name": test.name, "status": test.status}
  if test.reason:
    result["reason"] = test.reason
  if test.observed:
    result["observed"] = test.observed
  return result


def report_to_dict(report: ConformanceReport) -> dict[str, Any]:
  """Serializes a ConformanceReport to a JSON-compatible dictionary.

  Args:
    report: ConformanceReport instance to serialize.

  Returns:
    Dictionary suitable for json.dump / json.dumps.
  """
  result: dict[str, Any] = {
      "runtime": report.runtime,
      "config": report.config,
      "runtime_version": report.runtime_version,
      "spec_commit": report.spec_commit,
      "configured_skips": list(report.configured_skips),
  }
  if report.skip_reasons:
    result["skip_reasons"] = dict(report.skip_reasons)
  result["suites"] = [
      {
          "name": suite.name,
          "file": suite.file,
          "enabled": suite.enabled,
          "sections": [
              {
                  "name": sec.name,
                  "tests": [_test_to_dict(t) for t in sec.tests],
              }
              for sec in suite.sections
          ],
      }
      for suite in report.suites
  ]
  return result


def load_report(path: str) -> ConformanceReport:
  """Loads a ConformanceReport from a JSON file.

  Args:
    path: Filesystem path to a conformance report JSON file.

  Returns:
    Parsed ConformanceReport instance.

  Raises:
    ValueError: If any test entry has a status not in VALID_TEST_STATUSES.
  """
  with open(path, "r", encoding="utf-8") as f:
    return report_from_dict(json.load(f))


def load_reports(
    report_paths: Sequence[str],
) -> dict[tuple[str, str], ConformanceReport]:
  """Loads multiple ConformanceReport JSON files keyed by (runtime, config).

  Args:
    report_paths: Sequence of filesystem paths to conformance report JSON files.

  Returns:
    Mapping from (runtime, config) tuple to ConformanceReport.

  Raises:
    ValueError: If two reports share the same (runtime, config) key, or if any
      report contains a test status not in VALID_TEST_STATUSES.
  """
  reports: dict[tuple[str, str], ConformanceReport] = {}
  for path in report_paths:
    report = load_report(path)
    key = (report.runtime, report.config)
    if key in reports:
      raise ValueError(
          f"Duplicate conformance report for runtime={report.runtime!r},"
          f" config={report.config!r}: {path}"
      )
    reports[key] = report
  return reports


def _resolve_disabled_tests(tests: Sequence[TestResult]) -> CellStatus:
  """Resolves a cell for a disabled suite (enabled == False).

  Args:
    tests: Test results belonging to the disabled section or suite.

  Returns:
    Resolved CellStatus with category 'not_enabled' (including dashboard-mode
    execution counts when tests were executed).
  """
  total = len(tests)
  has_executed = False
  passed = 0
  failed = 0
  skipped = 0
  unexecuted = 0
  for t in tests:
    if t.status in ("pass", "fail", "skipped"):
      has_executed = True
    if t.status == "pass" or (t.status == "skipped" and t.observed == "pass"):
      passed += 1
    elif t.status == "fail" or (t.status == "skipped" and t.observed == "fail"):
      failed += 1
    elif t.status == "skipped" and not t.observed:
      skipped += 1
    elif t.status in ("not_enabled", "not_run"):
      unexecuted += 1

  if not has_executed or total == 0:
    return CellStatus(
        category="not_enabled", rendered="⬜", passed=0, total=total
    )
  if failed > 0:
    return CellStatus(
        category="not_enabled",
        rendered=f"⬜ (❌ {passed}/{total})",
        passed=passed,
        total=total,
    )
  if skipped > 0 or unexecuted > 0 or passed < total:
    return CellStatus(
        category="not_enabled",
        rendered=f"⬜ (⚠️ {passed}/{total})",
        passed=passed,
        total=total,
    )
  return CellStatus(
      category="not_enabled",
      rendered=f"⬜ (✅ {passed}/{total})",
      passed=passed,
      total=total,
  )


def resolve_section_cell(
    report: ConformanceReport | None, suite_name: str, section_name: str
) -> CellStatus:
  """Resolves the status cell for a (suite, section) in a given column report.

  Precedence order:
    1. Corpus membership check -> `n/a`
    2. Suite not enabled -> `⬜` (or `⬜ (✅ X/Y)`, `⬜ (⚠️ X/Y)`, `⬜ (❌ X/Y)`
       if executed in dashboard mode)
    3. Not Run -> `❓`
    4. Failed (1+ tests failed in an enabled section) -> `❌ passed/total`
    5. Skipped (1+ tests skipped, 0 failed in an enabled section) ->
       `⚠️ passed/total`
    6. Passed (all tests passed in an enabled section) -> `✅ total/total`

  Args:
    report: The column's ConformanceReport, or None if missing.
    suite_name: Name of the suite.
    section_name: Name of the section, or '*' for a whole-suite row.

  Returns:
    The resolved CellStatus.
  """
  if report is None:
    return CellStatus(category="not_run", rendered="❓", passed=0, total=0)

  suite = report.get_suite(suite_name)
  if suite is None:
    return CellStatus(category="na", rendered="`n/a`", passed=0, total=0)

  if section_name == "*":
    if not suite.sections:
      return CellStatus(category="na", rendered="`n/a`", passed=0, total=0)
    all_tests: list[TestResult] = []
    for sec in suite.sections:
      all_tests.extend(sec.tests)
    tests: Sequence[TestResult] = all_tests
  else:
    section = suite.get_section(section_name)
    if section is None:
      return CellStatus(category="na", rendered="`n/a`", passed=0, total=0)
    tests = section.tests

  if not suite.enabled:
    return _resolve_disabled_tests(tests)

  total = len(tests)
  if total == 0:
    return CellStatus(category="na", rendered="`n/a`", passed=0, total=0)
  has_not_run = False
  passed = 0
  failed = 0
  skipped = 0
  not_enabled = 0
  for t in tests:
    if t.status == "not_run":
      has_not_run = True
    elif t.status == "not_enabled":
      not_enabled += 1
    elif t.status == "pass":
      passed += 1
    elif t.status == "fail":
      failed += 1
    elif t.status == "skipped":
      skipped += 1

  if has_not_run:
    return CellStatus(category="not_run", rendered="❓", passed=0, total=total)

  # In an enabled suite, tests in configured_skips have status == "skipped"
  # (even if observed == "fail" in dashboard mode) and intentionally resolve to
  # "skipped" (⚠️) rather than "fail" (❌); "fail" is reserved for unskipped
  # failures.
  if failed > 0:
    return CellStatus(
        category="fail",
        rendered=f"❌ {passed}/{total}",
        passed=passed,
        total=total,
    )
  if skipped > 0 or not_enabled > 0 or passed < total:
    return CellStatus(
        category="skipped",
        rendered=f"⚠️ {passed}/{total}",
        passed=passed,
        total=total,
    )
  return CellStatus(
      category="pass",
      rendered=f"✅ {passed}/{total}",
      passed=passed,
      total=total,
  )


_HRULE_RE = re.compile(r"-{3,}")
_TABLE_ALIGN_RE = re.compile(r":?-{3,}:?")
_WHITESPACE_RE = re.compile(r"\s+")
_BULLET_RE = re.compile(r"\*\s+.*")


def _iter_column_notes(
    suite_name: str,
    section_name: str,
    col: ManifestColumn,
    column_notes: Mapping[tuple[str, str], str] | None,
) -> Iterator[str]:
  """Yields matching column notes for (suite, section, col).

  Args:
    suite_name: Name of the suite.
    section_name: Name of the section, or '*' for a whole-suite row.
    col: ManifestColumn to match column keys against.
    column_notes: Mapping from (row_key, column_key) to note text, or None if no
      column notes are configured.

  Yields:
    Matching column-specific note strings in precedence order.
  """
  if not column_notes:
    return
  exact_key = f"{suite_name}/{section_name}"
  wildcard_key = f"{suite_name}/*"
  row_keys = (
      (exact_key,) if exact_key == wildcard_key else (exact_key, wildcard_key)
  )
  for row_k in row_keys:
    for col_k in (col.id, f"{col.runtime}/{col.config}", col.runtime):
      col_note = column_notes.get((row_k, col_k))
      if col_note:
        yield col_note


def _synthesize_fallback_column_notes(
    suite_name: str,
    section_name: str,
    manifest: Sequence[ManifestColumn],
    cells: Sequence[CellStatus],
    reports: Mapping[tuple[str, str], ConformanceReport],
    uncovered_col_ids: Set[str],
    expanded_skips: Mapping[tuple[str, str], Set[str]] | None = None,
) -> list[str]:
  """Synthesizes fallback notes for skipped columns lacking curated notes.

  Args:
    suite_name: Name of the suite.
    section_name: Name of the section, or '*' for a whole-suite row.
    manifest: Ordered sequence of ManifestColumn definitions.
    cells: Resolved CellStatus values corresponding to manifest columns.
    reports: Mapping from (runtime, config) to ConformanceReport.
    uncovered_col_ids: Column IDs needing fallback skip notes.
    expanded_skips: Optional pre-expanded skip sets keyed by (runtime, config).

  Returns:
    Generated fallback note strings, grouped by runtime when targets match.
  """
  col_targets: dict[str, str] = {}
  for col, cell in zip(manifest, cells, strict=True):
    if col.id not in uncovered_col_ids or cell.category != "skipped":
      continue
    rep = reports.get((col.runtime, col.config))
    target = ""
    if rep is not None:
      key = (col.runtime, col.config)
      col_skips = (
          expanded_skips[key]
          if expanded_skips is not None and key in expanded_skips
          else set(expand_skip_patterns(rep.configured_skips))
      )
      if suite_name in col_skips or f"{suite_name}/{section_name}" in col_skips:
        target = (
            f"`{suite_name}`" if section_name == "*" else f"`{section_name}`"
        )
      else:
        suite = rep.get_suite(suite_name)
        sec = suite.get_section(section_name) if suite is not None else None
        if sec is not None:
          skipped_tests = [
              f"`{t.name}`" for t in sec.tests if t.status == "skipped"
          ]
          if skipped_tests:
            target = ", ".join(skipped_tests)
    if not target:
      target = f"`{suite_name}`" if section_name == "*" else f"`{section_name}`"
    col_targets[col.id] = target

  notes: list[str] = []
  seen_grouped_runtimes: set[str] = set()

  for col, _ in zip(manifest, cells, strict=True):
    if col.id not in col_targets:
      continue
    target = col_targets[col.id]
    same_runtime_cols = [c for c in manifest if c.runtime == col.runtime]
    can_group = len(same_runtime_cols) > 1 and all(
        c.id in col_targets and col_targets[c.id] == target
        for c in same_runtime_cols
    )
    if can_group:
      if col.runtime not in seen_grouped_runtimes:
        seen_grouped_runtimes.add(col.runtime)
        label = _RUNTIME_LABELS.get(col.runtime, col.label)
        notes.append(f"{label} skips {target}")
    else:
      notes.append(f"{col.label} skips {target}")

  return notes


def lookup_note(
    notes: Mapping[str, str],
    suite_name: str,
    section_name: str,
    column_notes: Mapping[tuple[str, str], str] | None = None,
    manifest: Sequence[ManifestColumn] | None = None,
    cells: Sequence[CellStatus] | None = None,
    reports: Mapping[tuple[str, str], ConformanceReport] | None = None,
    expanded_skips: Mapping[tuple[str, str], Set[str]] | None = None,
) -> str:
  """Looks up a note for (suite, section), including per-column notes.

  Args:
    notes: Mapping from 'suite/section' (or 'suite/*') to shared note text.
    suite_name: Name of the suite.
    section_name: Name of the section, or '*' for a whole-suite row.
    column_notes: Optional mapping from (row_key, column_key) to column-specific
      note text. If None, only shared row notes are returned.
    manifest: Optional sequence of ManifestColumn definitions corresponding to
      cells. Required when resolving column_notes or synthesizing fallback notes
      via reports.
    cells: Optional sequence of resolved CellStatus objects for the row.
      Required when resolving column_notes or synthesizing fallback notes via
      reports.
    reports: Optional mapping from (runtime, config) to ConformanceReport. Used
      for automatic fallback note synthesis when a skipped column has no curated
      note.
    expanded_skips: Optional pre-expanded skip sets keyed by (runtime, config).

  Returns:
    Combined note string joined by '; ', or '' if no notes match.
  """
  parts: list[str] = []
  exact_key = f"{suite_name}/{section_name}"
  wildcard_key = f"{suite_name}/*"
  exact_note = notes.get(exact_key)
  if exact_note is not None:
    parts.append(exact_note)
    has_row_note = True
  else:
    wildcard_note = notes.get(wildcard_key)
    has_row_note = wildcard_note is not None
    if wildcard_note is not None:
      parts.append(wildcard_note)

  uncovered_col_ids: set[str] = set()

  if manifest and cells:
    for col, cell in zip(manifest, cells, strict=True):
      if cell.category == "pass":
        continue
      col_notes = (
          list(_iter_column_notes(suite_name, section_name, col, column_notes))
          if column_notes
          else []
      )
      for col_note in col_notes:
        if col_note not in parts:
          parts.append(col_note)
      if not has_row_note and cell.category == "skipped" and not col_notes:
        uncovered_col_ids.add(col.id)

    if uncovered_col_ids and reports is not None:
      fallback_notes = _synthesize_fallback_column_notes(
          suite_name,
          section_name,
          manifest,
          cells,
          reports,
          uncovered_col_ids,
          expanded_skips=expanded_skips,
      )
      for fn in fallback_notes:
        if fn not in parts:
          parts.append(fn)

  return "; ".join(parts)


def collect_corpus_union(
    manifest: Sequence[ManifestColumn],
    reports: Mapping[tuple[str, str], ConformanceReport],
    testdata_suites: Sequence[SuiteCorpus] | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
  """Collects the union of all suites, filenames, and ordered sections.

  Args:
    manifest: Sequence of expected ManifestColumn definitions.
    reports: Mapping from (runtime, config) to ConformanceReport.
    testdata_suites: Optional sequence of parsed (suite_name, filename,
      sections) tuples from the .textproto corpus. If None, only sections
      present in reports are collected.

  Returns:
    A tuple of (suite_files, suite_sections), where suite_files maps suite_name
    to its .textproto filename and suite_sections maps suite_name to its ordered
    list of non-excluded section names.
  """
  excluded_sections: set[str] = set()
  for col in manifest:
    excluded_sections.update(col.excluded_sections)
  suite_files: dict[str, str] = {}
  suite_sections: dict[str, list[str]] = {}
  seen_sections: set[tuple[str, str]] = set()

  if testdata_suites:
    for suite_name, filename, sections in testdata_suites:
      suite_files[suite_name] = filename
      sec_list = suite_sections.setdefault(suite_name, [])
      for sec_name, _ in sections:
        if f"{suite_name}/{sec_name}" in excluded_sections:
          continue
        pair = (suite_name, sec_name)
        if pair not in seen_sections:
          seen_sections.add(pair)
          sec_list.append(sec_name)

  ordered_reports = (
      *(reports.get((col.runtime, col.config)) for col in manifest),
      *reports.values(),
  )
  for report in ordered_reports:
    if report is None:
      continue
    for suite in report.suites:
      suite_files.setdefault(suite.name, suite.file)
      sec_list = suite_sections.setdefault(suite.name, [])
      for sec in suite.sections:
        if f"{suite.name}/{sec.name}" in excluded_sections:
          continue
        pair = (suite.name, sec.name)
        if pair not in seen_sections:
          seen_sections.add(pair)
          sec_list.append(sec.name)

  return suite_files, suite_sections


def _render_header_and_legend(synthesized: bool) -> list[str]:
  """Returns the Markdown header, optional synthesis banner, and legend.

  Args:
    synthesized: Whether reports were synthesized from the manifest rather than
      loaded from executed runtime results.

  Returns:
    Markdown lines for the document header and status legend.
  """
  lines: list[str] = [
      "# CEL Conformance Coverage",
      "",
      (
          "This document tracks conformance test coverage across official CEL"
          " runtime\nimplementations against the\n[`cel-spec` simple"
          f" conformance test suites]({GITHUB_TESTDATA_BASE_URL})."
      ),
      "",
  ]
  if synthesized:
    note_text = (
        "**Note:** This matrix is derived from each runtime release's"
        " checked-in conformance configuration (enabled test files and skip"
        " lists), not from executed test results. Section totals are computed"
        " against each runtime's pinned `cel-spec` release (listed in the"
        " **Spec Commit** row); suites and sections added at newer `cel-spec`"
        " revisions are shown as `n/a`, and individual tests added to existing"
        " sections after a runtime's pin are excluded via `absent_tests`."
    )
    lines.extend([
        textwrap.fill(
            note_text,
            width=80,
            initial_indent="> ",
            subsequent_indent="> ",
            break_long_words=False,
            break_on_hyphens=False,
        ),
        "",
    ])
  lines.extend([
      "**Status Legend:**",
      "",
      (
          "*   `✅ X/Y`: **Passed** — All `Y` tests in the section are enabled"
          " and passing\n    (`X == Y`)."
      ),
      (
          "*   `❌ X/Y`: **Failed** — One or more tests in the section failed"
          " during\n    execution."
      ),
      (
          "*   `⚠️ X/Y`: **Skipped** — `X` of `Y` tests pass; the remaining"
          " `Y - X` tests\n    are explicitly skipped via `skip_tests` (see the"
          " `Notes` column and details\n    below)."
      ),
      (
          "*   `⬜`: **Not Enabled** — The `.textproto` test file is not yet"
          " wired into the\n    runtime's `_ALL_TESTS` conformance target. When"
          " executed in dashboard mode,\n    `⬜ (✅ X/Y)` indicates **Passing"
          " but Not Enabled**, `⬜ (⚠️ X/Y)` indicates\n    **Skipped and Not"
          " Enabled**, and `⬜ (❌ X/Y)` indicates **Failing and Not\n   "
          " Enabled**."
      ),
      (
          "*   `❓`: **Not Run** — Expected `(runtime, config)` column had no"
          " test results\n    reported."
      ),
      (
          "*   `` `n/a` ``: **Not Applicable** — The suite or section does not"
          " exist at the\n    `cel-spec` commit tested by that runtime."
      ),
      "",
  ])
  return lines


def _can_collapse_suite_wildcard(
    suite_name: str,
    manifest: Sequence[ManifestColumn],
    reports: Mapping[tuple[str, str], ConformanceReport],
) -> bool:
  """Returns True if no column enables or executes the given suite.

  Args:
    suite_name: Name of the conformance test suite under consideration.
    manifest: Ordered sequence of ManifestColumn definitions.
    reports: Mapping from (runtime, config) to ConformanceReport.

  Returns:
    True if all manifest columns have a report where suite_name is either
    absent or disabled with no executed ('pass', 'fail', 'skipped') tests.
  """
  for col in manifest:
    rep = reports.get((col.runtime, col.config))
    if rep is None:
      return False
    suite_obj = rep.get_suite(suite_name)
    if suite_obj is None:
      continue
    if suite_obj.enabled:
      return False
    for sec in suite_obj.sections:
      if any(t.status in ("pass", "fail", "skipped") for t in sec.tests):
        return False
  return True


def _render_callouts(callouts: Sequence[Callout]) -> list[str]:
  """Renders the Non-Conforming Suites & Known Issues callout section.

  Args:
    callouts: Sequence of Callout objects from conformance_notes.yaml.

  Returns:
    Markdown lines for the callouts section, or an empty list if none exist.
  """
  if not callouts:
    return []
  lines: list[str] = [
      "",
      "-----",
      "",
      "### Non-Conforming Suites & Known Issues",
      "",
  ]
  for callout in callouts:
    lines.append(
        textwrap.fill(
            f"**{callout.title}**",
            width=80,
            initial_indent="> ",
            subsequent_indent="> ",
            break_long_words=False,
            break_on_hyphens=False,
        )
    )
    lines.append(">")
    for label, value in (
        ("Runtimes", callout.runtimes),
        ("Tracking", callout.tracking),
        ("Details", callout.details),
    ):
      if value:
        lines.append(
            textwrap.fill(
                f"**{label}:** {value}",
                width=80,
                initial_indent="> *   ",
                subsequent_indent=">     ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    lines.append("")
  return lines


def _escape_table_cell(text: str) -> str:
  r"""Escapes newlines and unescaped pipes for Markdown table cells.

  Args:
    text: Raw cell text to sanitize.

  Returns:
    Single-line string with unescaped pipes escaped as '\|'.
  """
  single_line = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
  return _UNESCAPED_PIPE_RE.sub(lambda _: r"\|", single_line)


def render_markdown(
    manifest: Sequence[ManifestColumn],
    reports: Mapping[tuple[str, str], ConformanceReport],
    notes_config: NotesConfig,
    testdata_suites: Sequence[SuiteCorpus] | None = None,
    synthesized: bool = False,
) -> tuple[
    str,
    list[tuple[str, str]],
    dict[tuple[str, str], list[CellStatus]],
]:
  """Renders the conformance coverage markdown document.

  Args:
    manifest: Ordered sequence of expected ManifestColumns.
    reports: Loaded ConformanceReport objects keyed by (runtime, config).
    notes_config: Loaded NotesConfig from conformance_notes.yaml.
    testdata_suites: Optional parsed testdata suites for filename/section union.
      If None, only suites and sections from reports are rendered.
    synthesized: Whether the reports were synthesized from the manifest rather
      than ingested from executed runtime results. When true, an explanatory
      banner is emitted describing how the counts were derived.

  Returns:
    A tuple of (markdown_text, rendered_rows, rendered_cells_by_row).
  """
  suite_files, suite_sections = collect_corpus_union(
      manifest, reports, testdata_suites
  )

  lines = _render_header_and_legend(synthesized)

  col_headers = [_escape_table_cell(col.label) for col in manifest]
  lines.append(
      "| Suite (`*.textproto`) | Section | "
      + " | ".join(col_headers)
      + " | Notes |"
  )
  lines.append(
      "| :--- | :--- | " + " | ".join([":---"] * len(manifest)) + " | :--- |"
  )

  # Metadata rows: Runtime Version and Spec Commit
  version_cells: list[str] = []
  commit_cells: list[str] = []
  for col in manifest:
    rep = reports.get((col.runtime, col.config))
    if rep is None:
      version_cells.append("`n/a`")
      commit_cells.append("`n/a`")
    else:
      version_cells.append(f"`{_escape_table_cell(rep.runtime_version)}`")
      commit_cells.append(f"`{_escape_table_cell(rep.spec_commit)}`")

  lines.append(
      "| **Runtime Version** | — | "
      + " | ".join(version_cells)
      + " | Released / tested runtime version |"
  )
  lines.append(
      "| **Spec Commit** | — | "
      + " | ".join(commit_cells)
      + " | `cel-spec` revision tested |"
  )

  expanded_skips: dict[tuple[str, str], set[str]] | None = None
  if synthesized:
    expanded_skips = {}
    for col in manifest:
      rep = reports.get((col.runtime, col.config))
      if rep is not None:
        expanded_skips[(col.runtime, col.config)] = set(
            expand_skip_patterns(rep.configured_skips)
        )

  rendered_rows: list[tuple[str, str]] = []
  rendered_cells: dict[tuple[str, str], list[CellStatus]] = {}

  for suite_name in sorted(suite_sections.keys()):
    filename = suite_files.get(
        suite_name,
        _KNOWN_SUITE_TO_FILENAME.get(suite_name, f"{suite_name}.textproto"),
    )
    file_url = f"{GITHUB_TESTDATA_BASE_URL}/{filename}"
    suite_cell = f"[`{suite_name}`]({file_url})"
    sections = suite_sections[suite_name]

    collapse = bool(
        sections and _can_collapse_suite_wildcard(suite_name, manifest, reports)
    )
    row_sections = ["*"] if collapse else sections
    for sec_name in row_sections:
      row_key = (suite_name, sec_name)
      cells = [
          resolve_section_cell(
              reports.get((col.runtime, col.config)), suite_name, sec_name
          )
          for col in manifest
      ]
      rendered_rows.append(row_key)
      rendered_cells[row_key] = cells

      note = ""
      if sec_name == "*" or any(c.category != "pass" for c in cells):
        note = _escape_table_cell(
            lookup_note(
                notes_config.notes,
                suite_name,
                sec_name,
                notes_config.column_notes,
                manifest,
                cells,
                reports=reports if synthesized else None,
                expanded_skips=expanded_skips,
            )
        )
      sec_cell = f"[`{sec_name}`]({file_url})"
      lines.append(
          f"| {suite_cell} | {sec_cell} | "
          + " | ".join(c.rendered for c in cells)
          + f" | {note} |"
      )

  lines.extend(_render_callouts(notes_config.callouts))

  markdown = "\n".join(lines).rstrip() + "\n"
  return markdown, rendered_rows, rendered_cells


def normalize_markdown(markdown: str) -> str:
  """Normalizes markdown table padding and prose wrapping for comparison.

  Args:
    markdown: Raw Markdown document text.

  Returns:
    Canonicalized Markdown string with normalized table alignment, collapsed
    prose line wraps, and standardized horizontal rules.
  """
  out_lines: list[str] = []
  paragraph_buf: list[str] = []

  def _flush_paragraph() -> None:
    if paragraph_buf:
      out_lines.append(" ".join(paragraph_buf))
      paragraph_buf.clear()

  for raw_line in markdown.splitlines():
    line = raw_line.strip()
    if not line:
      _flush_paragraph()
      out_lines.append("")
      continue
    if _HRULE_RE.fullmatch(line):
      _flush_paragraph()
      out_lines.append("---")
      continue
    if line.startswith("|") and line.endswith("|"):
      _flush_paragraph()
      cells = [cell.strip() for cell in _UNESCAPED_PIPE_RE.split(line[1:-1])]
      norm_cells: list[str] = []
      for cell in cells:
        if _TABLE_ALIGN_RE.fullmatch(cell):
          left = ":" if cell.startswith(":") else ""
          right = ":" if cell.endswith(":") else ""
          norm_cells.append(f"{left}---{right}")
        else:
          norm_cells.append(cell)
      out_lines.append("| " + " | ".join(norm_cells) + " |")
      continue
    if line.startswith("#") or line.startswith("<!--"):
      _flush_paragraph()
      out_lines.append(line)
      continue
    if line.startswith(">"):
      inner = line[1:].strip()
      if not inner:
        _flush_paragraph()
        out_lines.append(">")
      elif _BULLET_RE.match(inner):
        _flush_paragraph()
        paragraph_buf.append("> " + _WHITESPACE_RE.sub(" ", inner))
      else:
        if paragraph_buf and paragraph_buf[0].startswith(">"):
          paragraph_buf.append(_WHITESPACE_RE.sub(" ", inner))
        else:
          _flush_paragraph()
          paragraph_buf.append("> " + _WHITESPACE_RE.sub(" ", inner))
      continue
    if _BULLET_RE.match(line):
      _flush_paragraph()
      paragraph_buf.append(_WHITESPACE_RE.sub(" ", line))
      continue

    paragraph_buf.append(_WHITESPACE_RE.sub(" ", line))

  _flush_paragraph()
  return "\n".join(out_lines).strip() + "\n"


def synthesize_report_from_corpus(
    runtime: str,
    config: str,
    runtime_version: str,
    spec_commit: str,
    testdata_suites: Sequence[SuiteCorpus],
    enabled_files: Set[str],
    configured_skips: Sequence[str],
    dashboard_executed_disabled: bool = False,
) -> ConformanceReport:
  """Synthesizes a ConformanceReport from testdata corpus and skip config.

  Args:
    runtime: Runtime name (e.g. 'go').
    config: Configuration variant (e.g. 'default').
    runtime_version: Version string or tag for the runtime.
    spec_commit: Pinned cel-spec commit SHA or tag.
    testdata_suites: Sequence of parsed (suite_name, filename, sections) tuples.
    enabled_files: Set of .textproto filenames enabled for this column.
    configured_skips: Sequence of skip prefix patterns for this column.
    dashboard_executed_disabled: If True, disabled suites are synthesized with
      'pass'/'skipped' test statuses instead of 'not_enabled'.

  Returns:
    Synthesized ConformanceReport instance.
  """
  skip_set = set(expand_skip_patterns(configured_skips))
  suite_reports: list[SuiteReport] = []
  for suite_name, filename, sections in testdata_suites:
    enabled = filename in enabled_files
    suite_skipped = suite_name in skip_set
    sec_reports: list[SectionReport] = []
    for sec_name, test_names in sections:
      if not enabled and not dashboard_executed_disabled:
        test_results = [
            TestResult(name=t_name, status="not_enabled")
            for t_name in test_names
        ]
      else:
        sec_path = f"{suite_name}/{sec_name}"
        sec_skipped = suite_skipped or (sec_path in skip_set)
        test_results = []
        for t_name in test_names:
          is_skipped = sec_skipped or _is_test_path_skipped(
              f"{sec_path}/{t_name}", skip_set
          )
          status = "skipped" if is_skipped else "pass"
          test_results.append(TestResult(name=t_name, status=status))
      sec_reports.append(
          SectionReport(name=sec_name, tests=tuple(test_results))
      )
    suite_reports.append(
        SuiteReport(
            name=suite_name,
            file=filename,
            enabled=enabled,
            sections=tuple(sec_reports),
        )
    )

  return ConformanceReport(
      runtime=runtime,
      config=config,
      runtime_version=runtime_version,
      spec_commit=spec_commit,
      configured_skips=tuple(configured_skips),
      suites=tuple(suite_reports),
  )


def synthesize_reports_from_manifest(
    manifest: Sequence[ManifestColumn],
    testdata_suites: Sequence[SuiteCorpus],
    runtime_version: str | None = None,
    spec_commit: str | None = None,
) -> dict[tuple[str, str], ConformanceReport]:
  """Synthesizes baseline ConformanceReports from manifest and testdata.

  Args:
    manifest: Ordered sequence of expected ManifestColumns.
    testdata_suites: Parsed testdata corpus.
    runtime_version: Optional override for every column's runtime_version;
      defaults to each column's manifest value.
    spec_commit: Optional override for every column's spec_commit; defaults to
      each column's manifest value.

  Returns:
    Synthesized ConformanceReports keyed by (runtime, config). Suites,
    sections, and tests listed in a column's absent_suites, absent_sections,
    or absent_tests are omitted (with absent suites and sections rendering as
    `n/a` for that column).
  """
  all_files = {filename for _, filename, _ in testdata_suites}
  reports: dict[tuple[str, str], ConformanceReport] = {}
  for col in manifest:
    absent_files = set(col.absent_suites)
    absent_sections = set(col.absent_sections)
    absent_tests = set(col.absent_tests)
    column_suites = []
    for suite_name, filename, sections in testdata_suites:
      if filename in absent_files:
        continue
      kept = []
      for sec_name, tests in sections:
        if f"{suite_name}/{sec_name}" in absent_sections:
          continue
        kept_tests = [
            t_name
            for t_name in tests
            if f"{suite_name}/{sec_name}/{t_name}" not in absent_tests
        ]
        if kept_tests:
          kept.append((sec_name, kept_tests))
      column_suites.append((suite_name, filename, kept))
    enabled_files = all_files - set(col.disabled_suites) - absent_files
    reports[(col.runtime, col.config)] = synthesize_report_from_corpus(
        runtime=col.runtime,
        config=col.config,
        runtime_version=runtime_version or col.runtime_version,
        spec_commit=spec_commit or col.spec_commit,
        testdata_suites=column_suites,
        enabled_files=enabled_files,
        configured_skips=col.configured_skips,
    )
  return reports


_MARKDOWN_LINK_RE = re.compile(r"^\[`([^`]+)`\]\(")
_INTERNAL_BUG_RE = re.compile(r"\bb/\d+\b")
_COUNTS_RE = re.compile(r"(\d+)/(\d+)")
_STATUS_CELL_RE = re.compile(r"(✅|⚠️|❌)\s+(\d+)/(\d+)")


def _add_section_test_prefixes(
    prefixes: set[str],
    suite_name: str,
    sections: Iterable[tuple[str, Iterable[str]]],
) -> None:
  """Adds suite, section, and test prefixes for a suite to prefixes.

  Args:
    prefixes: Destination set updated in place with valid prefix paths.
    suite_name: Name of the suite owning sections.
    sections: Iterable of (section_name, test_names) pairs.
  """
  for sec_name, test_names in sections:
    sec_prefix = f"{suite_name}/{sec_name}"
    added_any = False
    for test_name in test_names:
      if not added_any:
        prefixes.add(suite_name)
        prefixes.add(sec_prefix)
        added_any = True
      prefixes.update(_iter_path_prefixes(f"{sec_prefix}/{test_name}"))


def collect_corpus_prefixes(
    testdata_suites: Sequence[SuiteCorpus],
) -> set[str]:
  """Collects all valid suite, section, and test path prefixes from testdata.

  Args:
    testdata_suites: Sequence of parsed (suite_name, filename, sections) tuples
      from the testdata corpus.

  Returns:
    Set of valid suite, 'suite/section', and test path prefixes in the corpus.
  """
  valid_prefixes: set[str] = set()
  for suite_name, _, sections in testdata_suites:
    _add_section_test_prefixes(valid_prefixes, suite_name, sections)
  return valid_prefixes


def validate_skips(
    report: ConformanceReport,
    testdata_suites: Sequence[SuiteCorpus] | None = None,
    manifest_column: ManifestColumn | None = None,
    corpus_prefixes: Set[str] | None = None,
) -> list[str]:
  """Validates that every configured_skip matches at least one corpus test.

  Args:
    report: ConformanceReport whose configured_skips should be checked.
    testdata_suites: Optional full parsed testdata corpus so skip patterns
      referencing suites, sections, or tests omitted from `report` during
      synthesis are still recognized.
    manifest_column: Optional ManifestColumn providing absent suites, sections,
      and tests stripped during report synthesis.
    corpus_prefixes: Optional precomputed set of valid testdata corpus prefixes.
      If provided, avoids re-scanning testdata_suites for each report.

  Returns:
    A list of validation error messages (empty if all skip patterns match).
  """
  base_prefixes: Set[str]
  if corpus_prefixes is not None:
    base_prefixes = corpus_prefixes
  elif testdata_suites is not None:
    base_prefixes = collect_corpus_prefixes(testdata_suites)
  else:
    base_prefixes = frozenset()

  extra_prefixes: set[str] = set()
  filename_to_suite: dict[str, str] = dict(_KNOWN_FILENAME_TO_SUITE)
  for suite in report.suites:
    filename_to_suite[suite.file] = suite.name
    if suite.sections:
      _add_section_test_prefixes(
          extra_prefixes,
          suite.name,
          ((sec.name, (t.name for t in sec.tests)) for sec in suite.sections),
      )

  allow_absent_prefix_wildcard = (
      corpus_prefixes is None and testdata_suites is None
  )
  absent_prefixes: tuple[str, ...] = ()
  if manifest_column is not None:
    if manifest_column.absent_suites and testdata_suites is not None:
      for suite_name, filename, _ in testdata_suites:
        filename_to_suite[filename] = suite_name
    absent_prefix_list: list[str] = []
    for absent_file in manifest_column.absent_suites:
      suite_name = filename_to_suite.get(
          absent_file, absent_file.removesuffix(".textproto")
      )
      extra_prefixes.add(suite_name)
      if allow_absent_prefix_wildcard:
        absent_prefix_list.append(f"{suite_name}/")
    for absent_sec in manifest_column.absent_sections:
      extra_prefixes.add(absent_sec)
      if "/" in absent_sec:
        extra_prefixes.add(absent_sec.split("/", 1)[0])
      if allow_absent_prefix_wildcard:
        absent_prefix_list.append(f"{absent_sec}/")
    for absent_test in manifest_column.absent_tests:
      extra_prefixes.update(_iter_path_prefixes(absent_test))
    absent_prefixes = tuple(absent_prefix_list)

  errors: list[str] = []
  expanded_patterns = expand_skip_patterns(report.configured_skips)
  for pattern in expanded_patterns:
    if (
        pattern not in base_prefixes
        and pattern not in extra_prefixes
        and not (absent_prefixes and pattern.startswith(absent_prefixes))
    ):
      errors.append(
          f"[{report.runtime}/{report.config}] Unmatched skip pattern:"
          f" {pattern!r} does not match any (suite, section, test) in corpus."
      )
  return errors


def validate_manifest(
    manifest: Sequence[ManifestColumn],
    reports: Mapping[tuple[str, str], ConformanceReport],
) -> list[str]:
  """Validates that manifest columns have reports and enabled suites ran.

  Args:
    manifest: Ordered sequence of ManifestColumn definitions.
    reports: Mapping from (runtime, config) to ConformanceReport.

  Returns:
    A list of validation error messages.
  """
  errors: list[str] = []
  expected_keys = {(col.runtime, col.config) for col in manifest}
  for runtime, config in reports:
    if (runtime, config) not in expected_keys:
      errors.append(
          f"Unrecognized conformance report for runtime={runtime!r},"
          f" config={config!r} not present in manifest."
      )
  for col in manifest:
    report = reports.get((col.runtime, col.config))
    if report is None:
      errors.append(
          "Missing conformance report for expected manifest column"
          f" {col.label!r} (runtime={col.runtime!r}, config={col.config!r})."
      )
      continue
    for suite in report.suites:
      if not suite.enabled:
        continue
      for sec in suite.sections:
        if any(t.status == "not_run" for t in sec.tests):
          errors.append(
              f"[{col.runtime}/{col.config}] Suite/section"
              f" {suite.name}/{sec.name} has 'not_run' tests."
          )
        if any(t.status == "not_enabled" for t in sec.tests):
          errors.append(
              f"[{col.runtime}/{col.config}] Suite/section"
              f" {suite.name}/{sec.name} has 'not_enabled' tests in an"
              " enabled suite."
          )
  return errors


def validate_coverage(
    expected_sections: Sequence[tuple[str, str]],
    rendered_rows: Sequence[tuple[str, str]],
) -> list[str]:
  """Validates that every (suite, section) in the corpus union is rendered.

  Args:
    expected_sections: Expected (suite_name, section_name) pairs in the corpus.
    rendered_rows: (suite_name, section_name) rows emitted in the matrix.

  Returns:
    A list of coverage gap error messages.
  """
  rendered_set = set(rendered_rows)
  wildcard_suites = {suite for suite, sec in rendered_rows if sec == "*"}
  errors: list[str] = []
  for suite_name, sec_name in expected_sections:
    if (
        suite_name,
        sec_name,
    ) not in rendered_set and suite_name not in wildcard_suites:
      errors.append(
          f"Coverage gap: section '{suite_name}/{sec_name}' exists in corpus"
          " but is missing from rendered matrix rows."
      )
  return errors


def validate_notes(
    rendered_cells: Mapping[tuple[str, str], Sequence[CellStatus]],
    notes: Mapping[str, str],
    column_notes: Mapping[tuple[str, str], str] | None = None,
    manifest: Sequence[ManifestColumn] | None = None,
    callouts: Sequence[Callout] | None = None,
    reports: Mapping[tuple[str, str], ConformanceReport] | None = None,
) -> list[str]:
  """Validates that every ⚠️ cell has a note and no internal b/ links appear.

  Args:
    rendered_cells: Mapping from (suite_name, section_name) to per-column
      CellStatus values.
    notes: Row-level notes mapping.
    column_notes: Optional per-column notes mapping. When None, column-specific
      notes cannot be used to satisfy skip note requirements.
    manifest: Optional sequence of ManifestColumn definitions. When manifest is
      None, per-column skip note coverage cannot be checked and a row-level note
      in notes is required.
    callouts: Optional sequence of Callout blocks. When None, callout bug-link
      validation is skipped.
    reports: Optional mapping from (runtime, config) to ConformanceReport. When
      provided, automatic fallback note synthesis is used to satisfy missing
      note requirements.

  Returns:
    A list of validation error messages.
  """
  errors: list[str] = []
  for (suite_name, sec_name), cells in rendered_cells.items():
    if not any(c.category == "skipped" for c in cells):
      continue
    exact_key = f"{suite_name}/{sec_name}"
    wildcard_key = f"{suite_name}/*"
    has_row_note = bool(notes.get(exact_key) or notes.get(wildcard_key))
    if has_row_note:
      continue
    if manifest is not None and len(manifest) == len(cells):
      for col, cell in zip(manifest, cells, strict=True):
        if cell.category == "skipped" and not any(
            _iter_column_notes(suite_name, sec_name, col, column_notes)
        ):
          if reports is not None and (col.runtime, col.config) in reports:
            continue
          errors.append(
              "Missing required note in conformance_notes.yaml for skipped"
              f" section '{suite_name}/{sec_name}' (column {col.label!r})."
          )
    else:
      errors.append(
          "Missing required note in conformance_notes.yaml for skipped"
          f" section '{suite_name}/{sec_name}'."
      )

  for key, text in notes.items():
    if _INTERNAL_BUG_RE.search(text):
      errors.append(
          f"Internal bug link in note {key!r}: use public GitHub issue links."
      )
  if column_notes:
    for (row_k, col_k), text in column_notes.items():
      if _INTERNAL_BUG_RE.search(text):
        errors.append(
            f"Internal bug link in column note {row_k!r}/{col_k!r}: use public"
            " GitHub issue links."
        )
  if callouts:
    for callout in callouts:
      for field_val in (
          callout.title,
          callout.runtimes,
          callout.tracking,
          callout.details,
      ):
        if _INTERNAL_BUG_RE.search(field_val):
          errors.append(
              f"Internal bug link in callout {callout.title!r}: use"
              " public GitHub issue links."
          )
  return errors


def _is_regression_allowed(
    allowed_regressions: Mapping[str, Set[str]],
    suite_name: str,
    section_name: str,
    runtime: str,
    config: str,
    col_id: str = "",
) -> bool:
  """Returns True if a regression is allowlisted for (suite, section).

  Args:
    allowed_regressions: Mapping from 'suite/section' or 'suite/*' to allowed
      target keys ('*', column ID, 'runtime/config', or 'runtime').
    suite_name: Suite name of the cell being evaluated.
    section_name: Section name (or '*') of the cell being evaluated.
    runtime: Runtime identifier for the column.
    config: Configuration identifier for the column.
    col_id: Optional manifest column ID; defaults to empty string.

  Returns:
    True if either exact section or suite wildcard allowlists '*', col_id,
    'runtime/config', or 'runtime'.
  """
  target_id = f"{runtime}/{config}"
  for key in (f"{suite_name}/{section_name}", f"{suite_name}/*"):
    allowed_set = allowed_regressions.get(key, frozenset())
    if (
        "*" in allowed_set
        or target_id in allowed_set
        or runtime in allowed_set
        or (col_id and col_id in allowed_set)
    ):
      return True
  return False


def _has_cell_regressed(base_cell: CellStatus, curr_cell: CellStatus) -> bool:
  """Returns True if curr_cell represents a regression from base_cell.

  Args:
    base_cell: Baseline CellStatus parsed from the prior matrix.
    curr_cell: Newly resolved CellStatus for the same row and column.

  Returns:
    True if curr_cell degrades in category or non-passing count relative to
    base_cell (including fail/dashboard regressions), False otherwise.
  """
  if base_cell.category == "pass":
    return curr_cell.category != "pass"
  if base_cell.category == "skipped":
    if curr_cell.category in ("fail", "not_enabled", "not_run", "na"):
      return True
    if curr_cell.category == "skipped":
      base_skipped = base_cell.total - base_cell.passed
      curr_skipped = curr_cell.total - curr_cell.passed
      return curr_skipped > base_skipped
  if base_cell.category == "fail":
    if curr_cell.category in ("not_enabled", "not_run", "na"):
      return True
    if curr_cell.category == "fail":
      base_non_passing = base_cell.total - base_cell.passed
      curr_non_passing = curr_cell.total - curr_cell.passed
      return curr_non_passing > base_non_passing or (
          curr_cell.passed == 0 and base_cell.passed > 0
      )
  if base_cell.category == "not_enabled" and base_cell.total > 0:
    if curr_cell.category in ("not_run", "na", "fail"):
      return True
    if curr_cell.category == "not_enabled":
      if curr_cell.total == 0 or curr_cell.rendered == "⬜":
        return True
      if "❌" in curr_cell.rendered and "❌" not in base_cell.rendered:
        return True
      return (curr_cell.total - curr_cell.passed) > (
          base_cell.total - base_cell.passed
      )
    if curr_cell.category == "skipped":
      return (curr_cell.total - curr_cell.passed) > (
          base_cell.total - base_cell.passed
      )
  return False


def _format_cell_regression(
    col: ManifestColumn,
    suite_name: str,
    section_name: str,
    base_cell: CellStatus,
    curr_cell: CellStatus,
    allowed_regressions: Mapping[str, Set[str]],
) -> str | None:
  """Returns an error message if curr_cell unallowlisted-regressed.

  Args:
    col: ManifestColumn being evaluated.
    suite_name: Name of the suite.
    section_name: Name of the section, or '*' for a whole-suite row.
    base_cell: Baseline CellStatus from the committed markdown table.
    curr_cell: Current rendered CellStatus.
    allowed_regressions: Allowlisted regressions keyed by 'suite/section' or
      'suite/*'.

  Returns:
    Formatted regression error message, or None if curr_cell did not regress or
    the regression is allowlisted.
  """
  if not _has_cell_regressed(base_cell, curr_cell):
    return None
  if _is_regression_allowed(
      allowed_regressions,
      suite_name,
      section_name,
      col.runtime,
      col.config,
      col.id,
  ):
    return None
  return (
      f"[{col.runtime}/{col.config}] Regression in"
      f" '{suite_name}/{section_name}': baseline was"
      f" {base_cell.rendered} ({base_cell.category}), current is"
      f" {curr_cell.rendered} ({curr_cell.category})."
  )


def _parse_rendered_cell(rendered: str) -> CellStatus:
  """Parses a rendered Markdown cell string back into a CellStatus.

  Args:
    rendered: Raw or stripped Markdown table cell text.

  Returns:
    Parsed CellStatus (with category 'unknown' if the cell format is
    unrecognized).
  """
  text = rendered.strip()
  if text == "`n/a`":
    return CellStatus(category="na", rendered=text, passed=0, total=0)
  if text == "❓":
    return CellStatus(category="not_run", rendered=text, passed=0, total=0)
  if text.startswith("⬜"):
    match = _COUNTS_RE.search(text)
    passed = int(match.group(1)) if match else 0
    total = int(match.group(2)) if match else 0
    return CellStatus(
        category="not_enabled", rendered=text, passed=passed, total=total
    )
  match = _STATUS_CELL_RE.fullmatch(text)
  if match:
    symbol, passed_str, total_str = (
        match.group(1),
        match.group(2),
        match.group(3),
    )
    category = {"✅": "pass", "⚠️": "skipped", "❌": "fail"}[symbol]
    return CellStatus(
        category=category,
        rendered=text,
        passed=int(passed_str),
        total=int(total_str),
    )
  return CellStatus(category="unknown", rendered=text, passed=0, total=0)


def _parse_baseline_header_and_rows(
    markdown: str, manifest: Sequence[ManifestColumn]
) -> tuple[dict[str, int] | None, dict[tuple[str, str], list[CellStatus]]]:
  """Parses table header labels and rows from a baseline conformance.md.

  Args:
    markdown: Raw Markdown contents of the baseline conformance matrix.
    manifest: Ordered ManifestColumn sequence defining expected column labels
      and positional fallback width.

  Returns:
    Tuple (header_index_by_label, rows) where header_index_by_label maps
    unescaped column labels to table column indices (or None if no header row
    was found) and rows maps (suite, section) pairs to aligned CellStatus lists.
  """
  normalized = normalize_markdown(markdown)
  rows: dict[tuple[str, str], list[CellStatus]] = {}
  num_cols = len(manifest)
  header_index_by_label: dict[str, int] | None = None
  for line in normalized.splitlines():
    if not (line.startswith("|") and line.endswith("|")):
      continue
    cells = [cell.strip() for cell in _UNESCAPED_PIPE_RE.split(line[1:-1])]
    if (
        len(cells) >= 3
        and cells[0].startswith("Suite")
        and cells[1] == "Section"
    ):
      header_index_by_label = {
          label.replace(r"\|", "|"): idx
          for idx, label in enumerate(cells[2:-1], start=2)
      }
      continue
    if len(cells) < 3:
      continue
    suite_match = _MARKDOWN_LINK_RE.match(cells[0])
    section_match = _MARKDOWN_LINK_RE.match(cells[1])
    if not (suite_match and section_match):
      continue
    suite_name = suite_match.group(1)
    sec_name = section_match.group(1)
    if header_index_by_label is not None:
      col_cells: list[CellStatus] = []
      for col in manifest:
        col_idx = header_index_by_label.get(col.label)
        if col_idx is not None and col_idx < len(cells) - 1:
          col_cells.append(_parse_rendered_cell(cells[col_idx]))
        else:
          col_cells.append(
              CellStatus(category="unknown", rendered="", passed=0, total=0)
          )
    else:
      if len(cells) < 2 + num_cols:
        continue
      col_cells = [
          _parse_rendered_cell(cells[2 + idx]) for idx in range(num_cols)
      ]
    rows[(suite_name, sec_name)] = col_cells
  return header_index_by_label, rows


def parse_baseline_markdown(
    markdown: str, manifest: Sequence[ManifestColumn]
) -> dict[tuple[str, str], list[CellStatus]]:
  """Parses a committed conformance.md table into row_key -> [CellStatus].

  Args:
    markdown: Markdown document containing a conformance coverage table.
    manifest: Sequence of ManifestColumn definitions to align columns against.

  Returns:
    Mapping from (suite_name, section_name) to per-column CellStatus objects.
  """
  _, rows = _parse_baseline_header_and_rows(markdown, manifest)
  return rows


def validate_no_regressions_from_markdown(
    manifest: Sequence[ManifestColumn],
    rendered_cells: Mapping[tuple[str, str], Sequence[CellStatus]],
    baseline_markdown: str,
    allowed_regressions: Mapping[str, Set[str]],
) -> list[str]:
  """Compares current rendered cells against a baseline conformance.md.

  Args:
    manifest: Ordered sequence of ManifestColumn definitions.
    rendered_cells: Current rendered CellStatus values keyed by (suite_name,
      section_name).
    baseline_markdown: Committed Markdown string serving as the baseline.
    allowed_regressions: Allowlisted regressions keyed by suite/section.

  Returns:
    A list of regression error messages.
  """
  header_index_by_label, baseline_rows = _parse_baseline_header_and_rows(
      baseline_markdown, manifest
  )
  errors: list[str] = []
  if header_index_by_label is not None:
    for col in manifest:
      if col.label not in header_index_by_label:
        errors.append(
            f"Baseline markdown table header is missing column {col.label!r}"
            f" ({col.runtime}/{col.config})."
        )
  for (suite_name, sec_name), base_cells in baseline_rows.items():
    curr_cells = rendered_cells.get((suite_name, sec_name))
    if curr_cells is None:
      curr_cells = rendered_cells.get((suite_name, "*"))
    if curr_cells is None:
      curr_cells = [
          CellStatus(category="na", rendered="`n/a`", passed=0, total=0)
      ] * len(manifest)
    for idx, col in enumerate(manifest):
      if idx >= len(base_cells) or idx >= len(curr_cells):
        continue
      err = _format_cell_regression(
          col,
          suite_name,
          sec_name,
          base_cells[idx],
          curr_cells[idx],
          allowed_regressions,
      )
      if err is not None:
        errors.append(err)
  return errors


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
  """Parses CLI flags for generating and validating the conformance matrix.

  Args:
    argv: Optional sequence of CLI arguments; defaults to sys.argv[1:] when
      None.

  Returns:
    Parsed argparse.Namespace of CLI options.
  """
  parser = argparse.ArgumentParser(
      description="Generate and validate the CEL Conformance Coverage Matrix."
  )
  parser.add_argument(
      "--manifest",
      required=False,
      default=None,
      help="Path to conformance_manifest.yaml.",
  )
  parser.add_argument(
      "--notes",
      required=False,
      default=None,
      help="Path to conformance_notes.yaml.",
  )
  parser.add_argument(
      "--reports",
      nargs="*",
      default=[],
      help="Paths to conformance_report.json files.",
  )
  parser.add_argument(
      "--baseline_markdown",
      help="Optional path to baseline conformance.md for regression checks.",
  )
  parser.add_argument(
      "--testdata_dir",
      help="Optional path to tests/simple/testdata directory.",
  )
  parser.add_argument(
      "--output",
      help="Path to write generated markdown (prints to stdout if omitted).",
  )
  parser.add_argument(
      "--check_consistency",
      help=(
          "Path to existing markdown file to verify consistency against"
          " generated output."
      ),
  )
  parser.add_argument(
      "--strict_manifest",
      action="store_true",
      help="Fail if any expected column in the manifest is missing or not_run.",
  )
  parser.add_argument(
      "--no_validate_skips",
      action="store_true",
      help="Disable validation of unmatched skip patterns.",
  )
  parser.add_argument(
      "--no_validate_notes",
      action="store_true",
      help="Disable validation that every skipped section has a note.",
  )
  parser.add_argument(
      "--build_root",
      help="Optional workspace root directory for syncing BUILD skip lists.",
  )
  parser.add_argument(
      "--no_sync_build",
      action="store_true",
      help="Disable syncing configured_skips from runtime BUILD files.",
  )
  return parser.parse_args(argv)


def _resolve_workspace_args(args: argparse.Namespace) -> str | None:
  """Resolves default workspace paths on args and determines build_root.

  Args:
    args: Parsed CLI namespace; path attributes ('manifest', 'notes',
      'testdata_dir', 'output', 'check_consistency', 'baseline_markdown',
      'reports', 'build_root') are resolved in place against
      BUILD_WORKING_DIRECTORY and BUILD_WORKSPACE_DIRECTORY when relative.

  Returns:
    Resolved workspace or build root directory path for syncing runtime BUILD
    files, or None if it cannot be determined.
  """
  cwd_dir = os.environ.get("BUILD_WORKING_DIRECTORY")
  ws_dir = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
  spec_dir: str | None = None
  if ws_dir:
    spec_dir = ws_dir

    if not args.manifest:
      args.manifest = os.path.join(spec_dir, "tools/conformance_manifest.yaml")
    if not args.notes:
      args.notes = os.path.join(spec_dir, "tools/conformance_notes.yaml")
    if not args.testdata_dir and not args.reports:
      args.testdata_dir = os.path.join(spec_dir, "tests/simple/testdata")
    if not args.output and not args.check_consistency and not args.reports:
      args.output = os.path.join(spec_dir, "doc/conformance.md")

  if cwd_dir or ws_dir:

    def _resolve_rel(
        rel_path: str, fallback: str | None, is_dir: bool = False
    ) -> str:
      """Resolves a relative CLI path against workspace directories.

      Args:
        rel_path: Relative filesystem path supplied via CLI flags.
        fallback: Fallback base directory if rel_path does not exist yet.
        is_dir: If True, requires an existing directory match instead of file.

      Returns:
        Normalized resolved path under cwd_dir, ws_dir, spec_dir, or fallback.
      """
      exists_fn = os.path.isdir if is_dir else os.path.exists
      for base in (cwd_dir, ws_dir, spec_dir):
        if base:
          candidate = os.path.normpath(os.path.join(base, rel_path))
          if exists_fn(candidate):
            return candidate
      if fallback:
        return os.path.normpath(os.path.join(fallback, rel_path))
      return rel_path

    for attr in (
        "manifest",
        "notes",
        "testdata_dir",
        "output",
        "check_consistency",
        "baseline_markdown",
    ):
      p = getattr(args, attr, None)
      if p and not os.path.isabs(p):
        if attr == "output":
          base = spec_dir or cwd_dir or ws_dir
          if base:
            setattr(args, attr, os.path.normpath(os.path.join(base, p)))
        else:
          setattr(args, attr, _resolve_rel(p, spec_dir or ws_dir or cwd_dir))

    if getattr(args, "reports", None):
      report_fallback = cwd_dir or ws_dir or spec_dir
      args.reports = [
          r if os.path.isabs(r) else _resolve_rel(r, report_fallback)
          for r in args.reports
      ]

    if args.build_root and not os.path.isabs(args.build_root):
      args.build_root = _resolve_rel(
          args.build_root, cwd_dir or ws_dir or spec_dir, is_dir=True
      )

  if args.build_root:
    return args.build_root
  if ws_dir:
    return ws_dir
  return None


def _run_validation_gates(
    manifest: Sequence[ManifestColumn],
    reports: Mapping[tuple[str, str], ConformanceReport],
    notes_config: NotesConfig,
    testdata_suites: Sequence[SuiteCorpus] | None,
    rendered_rows: Sequence[tuple[str, str]],
    rendered_cells: Mapping[tuple[str, str], Sequence[CellStatus]],
    args: argparse.Namespace,
    base_md: str | None = None,
) -> list[str]:
  """Runs all validation gates and returns a list of error messages.

  Args:
    manifest: Ordered sequence of expected ManifestColumn definitions.
    reports: Mapping from (runtime, config) to ConformanceReport.
    notes_config: Parsed NotesConfig from conformance_notes.yaml.
    testdata_suites: Optional parsed testdata corpus, or None if unavailable.
    rendered_rows: Emitted (suite_name, section_name) rows in the matrix.
    rendered_cells: Mapping from (suite_name, section_name) to per-column
      CellStatus values.
    args: Parsed CLI flags controlling which optional gates are enabled.
    base_md: Optional baseline Markdown content for regression checks; skipped
      when None.

  Returns:
    Combined list of validation error messages (empty if all gates pass).
  """
  validation_errors: list[str] = []

  if not args.no_validate_skips:
    col_by_key = {(col.runtime, col.config): col for col in manifest}
    corpus_prefixes = (
        collect_corpus_prefixes(testdata_suites)
        if testdata_suites is not None
        else None
    )
    for rep in reports.values():
      validation_errors.extend(
          validate_skips(
              rep,
              testdata_suites=testdata_suites,
              manifest_column=col_by_key.get((rep.runtime, rep.config)),
              corpus_prefixes=corpus_prefixes,
          )
      )

  if args.strict_manifest:
    validation_errors.extend(validate_manifest(manifest, reports))

  _, suite_sections = collect_corpus_union(manifest, reports, testdata_suites)
  expected_sections: list[tuple[str, str]] = []
  for suite_name, secs in suite_sections.items():
    for sec_name in secs:
      expected_sections.append((suite_name, sec_name))
  validation_errors.extend(validate_coverage(expected_sections, rendered_rows))

  if not args.no_validate_notes:
    validation_errors.extend(
        validate_notes(
            rendered_cells,
            notes_config.notes,
            notes_config.column_notes,
            manifest,
            notes_config.callouts,
            reports=reports if not args.reports else None,
        )
    )

  if base_md is not None:
    validation_errors.extend(
        validate_no_regressions_from_markdown(
            manifest=manifest,
            rendered_cells=rendered_cells,
            baseline_markdown=base_md,
            allowed_regressions=notes_config.allowed_regressions,
        )
    )

  return validation_errors


def _check_markdown_consistency(
    existing_md: str, generated_md: str, fromfile: str
) -> str | None:
  """Checks consistency between existing and generated markdown.

  Args:
    existing_md: Raw text of the existing markdown file.
    generated_md: Newly generated markdown text.
    fromfile: Filename to display in the diff header.

  Returns:
    A unified diff string if there is a mismatch, or None if consistent.
  """
  norm_existing = normalize_markdown(existing_md)
  norm_generated = normalize_markdown(generated_md)
  if norm_existing != norm_generated:
    return "".join(
        difflib.unified_diff(
            norm_existing.splitlines(keepends=True),
            norm_generated.splitlines(keepends=True),
            fromfile=fromfile,
            tofile="generated",
        )
    )
  return None


def main(argv: Sequence[str] | None = None) -> int:
  """CLI entry point for generating and validating the conformance matrix.

  Args:
    argv: Optional sequence of command-line arguments.

  Returns:
    Exit code (0 on success, 1 on validation or consistency errors).
  """
  args = _parse_args(argv)
  build_root = _resolve_workspace_args(args)

  if not args.manifest or not args.notes:
    print(
        "ERROR: --manifest and --notes must be provided.",
        file=sys.stderr,
    )
    return 1

  if not args.reports and not args.testdata_dir:
    print(
        "ERROR: At least one of --reports or --testdata_dir must be provided.",
        file=sys.stderr,
    )
    return 1

  manifest = load_manifest(args.manifest)
  notes_config = load_notes(args.notes)

  testdata_suites = (
      load_testdata_corpus(args.testdata_dir) if args.testdata_dir else None
  )

  if (
      testdata_suites is not None
      and not args.no_sync_build
      and not args.reports
      and args.output
      and build_root
  ):
    if sync_manifest_from_build_files(
        args.manifest, testdata_suites, build_root
    ):
      manifest = load_manifest(args.manifest)

  if args.reports:
    reports = load_reports(args.reports)
  else:
    if testdata_suites is None:
      print(
          "ERROR: --testdata_dir is required when --reports is omitted.",
          file=sys.stderr,
      )
      return 1
    reports = synthesize_reports_from_manifest(manifest, testdata_suites)

  markdown, rendered_rows, rendered_cells = render_markdown(
      manifest=manifest,
      reports=reports,
      notes_config=notes_config,
      testdata_suites=testdata_suites,
      synthesized=not args.reports,
  )

  base_md = None
  if args.baseline_markdown:
    with open(args.baseline_markdown, "r", encoding="utf-8") as f:
      base_md = f.read()

  existing_md = None
  if args.check_consistency:
    with open(args.check_consistency, "r", encoding="utf-8") as f:
      existing_md = f.read()

  # Write --output after reading any baseline/consistency inputs (which may
  # point to the same path as --output) so partial/❓ matrices are still
  # emitted even when strict validation gates report errors.
  if args.output:
    with open(args.output, "w", encoding="utf-8") as f:
      f.write(markdown)

  validation_errors = _run_validation_gates(
      manifest=manifest,
      reports=reports,
      notes_config=notes_config,
      testdata_suites=testdata_suites,
      rendered_rows=rendered_rows,
      rendered_cells=rendered_cells,
      args=args,
      base_md=base_md,
  )
  if validation_errors:
    for err in validation_errors:
      print(f"ERROR: {err}", file=sys.stderr)
    return 1

  if existing_md is not None:
    diff = _check_markdown_consistency(
        existing_md, markdown, fromfile=args.check_consistency
    )
    if diff is not None:
      print(
          "ERROR: Conformance markdown is out of sync with reports and notes:\n"
          f"{diff}",
          file=sys.stderr,
      )
      return 1

  if not args.output and not args.check_consistency:
    sys.stdout.write(markdown)

  return 0


if __name__ == "__main__":
  sys.exit(main())
