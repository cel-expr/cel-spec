# CEL Conformance Coverage Matrix Generator

This directory contains the tooling and configuration used to generate and
validate the
[CEL Conformance Coverage Matrix](../doc/conformance.md).

## Files

*   `generate_conformance_matrix.py`: Parses the conformance test corpus (and
    optional runtime execution reports), runs governance validation gates, and
    renders
    [`doc/conformance.md`](../doc/conformance.md).
*   `conformance_manifest.yaml`: Declares the tracked CEL runtime
    implementations (`C++`, `Go`, `Java`), their release versions, and their
    checked-in conformance skip configurations (`disabled_suites`,
    `configured_skips`, `absent_suites`, `absent_sections`, and `absent_tests`).
*   `conformance_notes.yaml`: Provides human-readable explanations and public
    issue links for every non-passing (`skip`, `fail`, `partial`) cell in the
    matrix, as well as section-level callouts and `allowed_regressions`.
*   `generate_conformance_matrix_test.py`: Unit and end-to-end consistency tests
    for the generator, validation gates, and checked-in Markdown output.

## How It Works

1.  **Corpus Discovery (`--testdata_dir`)**: Parses all `.textproto` files under
    `tests/simple/testdata/` into `cel.expr.conformance.test.SimpleTestFile`
    messages to discover every suite, section, and test name.
2.  **BUILD File Skip and Suite Synchronization (One-Way Append)**: When run
    with workspace access to runtime `BUILD` files (`build_file`,
    `build_skip_var`, and `build_tests_var` declared in
    `conformance_manifest.yaml`), the generator parses runtime test lists and
    skip lists via Python AST analysis. Any `.textproto` suites omitted from
    `build_tests_var` (e.g. `_ALL_TESTS`) are appended to `disabled_suites`
    (`⬜`), while newly skipped test cases present in the corpus are appended to
    `configured_skips` (filtering out `ignored_build_skips`, absent sections,
    and disabled suites). Synchronization is intentionally one-way (append-only)
    so that new `HEAD` tests and skips are captured automatically without
    clobbering release-pinned or OSS-only skips in `conformance_manifest.yaml`.
3.  **Status Resolution (`--testdata_dir` vs. `--reports`)**:
    *   **Manifest Synthesis**: When run with `--testdata_dir` alone, each
        runtime column is synthesized from `conformance_manifest.yaml` by
        applying its `disabled_suites`, `configured_skips`, `absent_suites`,
        `absent_sections`, and `absent_tests` against the `.textproto` corpus.
    *   **Live Execution Reports**: When JSON execution reports are passed via
        `--reports`, observed test outcomes (`passed`, `failed`, `skipped`) are
        evaluated alongside the manifest configuration.
4.  **Automatic Fallback Note Synthesis**: When newly skipped sections lack a
    curated explanation in `conformance_notes.yaml`, the generator automatically
    synthesizes a fallback note identifying the skipped section or tests (e.g.
    ``C++ skips \`type_decay\`; Java skips \`type_decay\` ``), grouping matching
    configurations for the same runtime.
5.  **Validation Gates**:
    *   **Skip Validation**: Fails if any `configured_skips` entry does not
        match at least one test in the corpus.
    *   **Note Validation**: Fails if any skipped (`⚠️`) cell lacks an
        explanation in `conformance_notes.yaml` (or an auto-synthesized fallback
        note during manifest synthesis), or if a note contains internal bug
        references.
    *   **Coverage Validation**: Fails if any section in the `.textproto` corpus
        is missing from the rendered Markdown table.
    *   **Regression Validation**: When `--baseline_markdown` is supplied, fails
        if any cell regresses (for example, `pass` to `skip` or `fail`) unless
        explicitly permitted under `allowed_regressions` in
        `conformance_notes.yaml`.

## Usage

### With Bazel (Zero Flags Required)

With Bazel, developers can regenerate the conformance coverage matrix, sync
missing skips from runtime `BUILD` files, and update `doc/conformance.md`
with a single zero-flag command:

```bash
bazel run //tools:generate_conformance_matrix
```

The generator detects `BUILD_WORKSPACE_DIRECTORY`, resolves default paths to
`conformance_manifest.yaml`, `conformance_notes.yaml`, `tests/simple/testdata`,
and `doc/conformance.md`, synchronizes new skips from runtime `BUILD` files,
synthesizes fallback notes where needed, and writes the updated Markdown.

### Standalone / OSS Usage

Run from the repository root after modifying `conformance_manifest.yaml`,
`conformance_notes.yaml`, or any `.textproto` suite under
`tests/simple/testdata/`:

```bash
python3 tools/generate_conformance_matrix.py \
  --manifest tools/conformance_manifest.yaml \
  --notes tools/conformance_notes.yaml \
  --testdata_dir tests/simple/testdata \
  --output doc/conformance.md
```

### Verify Consistency in CI / Presubmit

To verify that all validation gates pass and
[`doc/conformance.md`](../doc/conformance.md) is up to
date without modifying files on disk:

```bash
python3 tools/generate_conformance_matrix.py \
  --manifest tools/conformance_manifest.yaml \
  --notes tools/conformance_notes.yaml \
  --testdata_dir tests/simple/testdata \
  --check_consistency doc/conformance.md
```

### Common Maintenance Workflows

*   **Adding new conformance tests or runtime skips**:
    1.  Add the test cases to `tests/simple/testdata/<suite>.textproto` and add
        any runtime skips (`<suite>/<section>` or `<suite>/<section>/<test>`) to
        the corresponding runtime `BUILD` file.
    2.  Run
        `bazel run //tools:generate_conformance_matrix`
        to automatically append the new skips or disabled suites into
        `tools/conformance_manifest.yaml`, synthesize fallback notes, and
        regenerate `doc/conformance.md`.
    3.  *(Optional)* Add a curated explanation or public GitHub issue link in
        `tools/conformance_notes.yaml` if needed.
*   **Fixing a conformance issue (enabling a skipped test or suite)**:
    1.  Remove the skip pattern or enable the suite in the runtime `BUILD` file,
        and manually remove the corresponding entry from `configured_skips` or
        `disabled_suites` in `tools/conformance_manifest.yaml` (since `BUILD`
        synchronization is append-only).
    2.  *(Optional)* Update or remove the corresponding entry in
        `tools/conformance_notes.yaml` (notes for sections that transition to
        `✅` are automatically omitted from `doc/conformance.md`).
    3.  Regenerate `doc/conformance.md`.
*   **Renaming or deleting tests in `.textproto`**:
    1.  Update `tests/simple/testdata/<suite>.textproto` and any corresponding
        runtime `BUILD` skip lists.
    2.  Remove or update any stale skip patterns in
        `tools/conformance_manifest.yaml` reported by the skip validation gate,
        then regenerate `doc/conformance.md`.
*   **Updating a pinned runtime release**:
    1.  Update `runtime_version`, `spec_commit`, `absent_*`, `disabled_suites`,
        and `configured_skips` for that column in
        `tools/conformance_manifest.yaml` to match the new release tag, then
        regenerate `doc/conformance.md`.
