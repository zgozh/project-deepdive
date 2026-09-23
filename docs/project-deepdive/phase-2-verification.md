# Project DeepDive Phase 2 — Verification Record

> Status: implementation complete, awaiting Principal Engineer review
> Date: 2026-09-22
> Scope: `docs/project-deepdive/plans/phase-2-repository-scanner-and-coverage.md` Task 1–8
> Evidence convention: every number below comes from an observed command run in this working tree. Nothing here is predicted.

## 1. What was implemented

One deterministic vertical slice:

```text
Git repository + optional exact-path overrides
→ tracked-file inventory (NUL-safe Git enumeration)
→ deterministic file typing (no host MIME registry)
→ repository-surface classification
→ project-index.json + coverage.json (schema 1.1.0)
→ cross-artifact + snapshot semantic audit (G01)
```

No AST, language/framework adapter, stack profile, runtime discovery, knowledge
graph, curriculum, handbook, VibeCoding, interview or dogfood behavior is
implemented. Phase 3 was not started.

## 2. Baseline (Task 0, Phase 1 entry gate)

### 2.1 Worktree recorded before any edit

```text
$ git status --short
 M README.md
 M skills/replicate-learning/SKILL.md
?? ACCEPTANCE_CRITERIA.md
?? AGENTS.md
?? ARCHITECTURE.md
?? IMPLEMENTATION_PLAN.md
?? PROJECT_DEEPDIVE_SPEC.md
?? QUALITY_GATES.md
?? REQUIREMENTS.md
?? VIBECODING_SPEC.md
?? docs/project-deepdive/
?? skills/replicate-learning/schemas/
?? skills/replicate-learning/scripts/artifact_contract.py
?? skills/replicate-learning/scripts/test_artifact_contract.py
?? skills/replicate-learning/scripts/validate_artifact.py
?? skills/replicate-learning/tests/fixtures/artifacts/
?? text.md

$ git diff --stat
 README.md                          | 23 +++++++++++++++++++----
 skills/replicate-learning/SKILL.md |  4 ++++
 2 files changed, 23 insertions(+), 4 deletions(-)

$ git rev-parse HEAD
1463a06437fc903edec24722ecbb686a46d8f9da
```

These pre-existing Phase 0/1 changes were not reset, cleaned, staged or overwritten.

### 2.2 Phase 1 focused and full checks (run from `skills/replicate-learning/`)

| Command | Exit | Observed |
|---|---:|---|
| `python scripts/validate_artifact.py <7 v1 fixtures>` | 0 | 7 `PASS` lines, 0 `FAIL` |
| `python -m unittest scripts.test_artifact_contract -v` | 0 | Ran 25 tests, OK |
| `python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts` | 0 | Ran 190 tests, OK |
| `python scripts/skill_selfcheck.py` | 0 | 检查项 122，失败 0 → PASS |
| `python scripts/v2_selfcheck.py` | 0 | V2 self-check: PASS (6 contract entries) |

Gate result: PASS. The seven v1 schemas are present, the Phase 1 fixtures validate,
all pre-existing tests are green, and no unrelated dirty change was touched.

## 3. Module summary

| File | Responsibility |
|---|---|
| `scripts/file_classification.py` | pure deterministic typing + ordered surface rules + override validation |
| `scripts/repository_scan.py` | Git context discovery, worktree/git-tree enumeration, hashing, strict override loading, artifact assembly |
| `scripts/coverage_audit.py` | reusable cross-artifact and snapshot semantic audit with stable violation codes |
| `scripts/scan_repository.py` | thin scan CLI with staged publication |
| `scripts/validate_coverage.py` | thin audit CLI |
| `schemas/v1/project-index.schema.json` | accepts exactly `1.0.0` and `1.1.0`, optional typing fields |
| `schemas/v1/coverage.schema.json` | accepts exactly `1.0.0` and `1.1.0`, optional policy/rule fields |

## 4. TDD evidence (Task 1–8)

| Task | First failing command | Failure reason observed | Fix | Green result |
|---|---|---|---|---|
| 1 | `python -m unittest scripts.test_artifact_contract.SchemaDocumentTests scripts.test_artifact_contract.ArtifactValidationTests -v` | `Ran 24 tests … FAILED`; 3 FAIL + 2 ERROR, e.g. `$.schema_version: expected constant '1.0.0'`, `$.files[0].content_kind: unexpected property`, `$.classification_policy_version: unexpected property` | enum version + 4 optional project-index fields + 3 optional coverage fields | `python -m unittest scripts.test_artifact_contract` → Ran 32 tests, OK |
| 2 | `python -m unittest scripts.test_file_classification -v` | `ModuleNotFoundError: No module named 'file_classification'` (Ran 1 test, FAILED) | implemented `file_classification.py` | Ran 29 tests, OK |
| 3 | **not separately captured — see §4.1** | — | implemented `repository_scan.py` | `python -m unittest scripts.test_repository_scan.GitInventoryTests -v` → all inventory/snapshot tests OK (23 tests in the module at that point, 3 platform skips) |
| 4 | `python -m unittest scripts.test_repository_scan.CoverageOverrideTests scripts.test_repository_scan.ArtifactAssemblyTests` | `Ran 2 tests … FAILED (errors=2)` — `ScanOptions` / `load_coverage_overrides` did not exist yet | strict override loader + artifact assembly | Ran 41 tests, OK (skipped=3) |
| 5 | `python -m unittest scripts.test_coverage_audit` | `Ran 1 test … FAILED (errors=1)` — `No module named 'coverage_audit'` | implemented `coverage_audit.py` | Ran 27 tests, OK |
| 6 | `python -m unittest scripts.test_repository_scan_cli` | `Ran 17 tests … FAILED (failures=17)` — both CLI scripts absent | implemented both CLIs + staged publication | Ran 17 tests, OK |
| 7 | **no red state exists — see §4.2** | — | fixtures + integration tests | `FixtureFamilyTests` 5 tests OK; fixture unknown/CLI tests 4 tests OK |
| 8 | n/a (documentation) | — | README / SKILL / coverage-policy / this record | `skill_selfcheck.py` exit 0 (122 checks, 0 failures, 28 tools no orphans); real CLI smoke scan below |

### 4.1 Deviation reported honestly: Task 3

The Task 3 test module was written before `repository_scan.py` existed, but the
"missing module" red run for that module was not executed as a separate command
before the implementation was written. The genuine, observed red for that same
file is the Task 4 run (`Ran 2 tests … FAILED (errors=2)`), which failed because
`repository_scan.py` did not yet expose `ScanOptions` / `load_coverage_overrides`.
No red log is reconstructed or invented for Task 3.

### 4.2 Deviation reported honestly: Task 7

Task 7 adds no new production behavior: it adds fixture templates plus integration
tests over the Task 1–6 implementation. The integration tests passed on their first
execution (`FixtureFamilyTests`: Ran 5 tests, OK; fixture unknown-resolution and CLI
tests: Ran 4 tests, OK), so there is no red→green transition to report. Had the
fixture templates been missing, `build_fixture_repository` would have raised
`FileNotFoundError`; that run was not performed.

## 5. Fixture evidence

Each template is copied into a temporary directory and initialized as an isolated
Git repository (`user.name`/`user.email`/`core.autocrlf=false` set locally) before
scanning.

| Fixture family | `git ls-files -z` count | `project-index.file_count` | `coverage.tracked_file_count` | `unknown_count` |
|---|---:|---:|---:|---:|
| `python` | 7 | 7 | 7 | 0 |
| `java` | 7 | 7 | 7 | 0 |
| `frontend` | 11 | 11 | 11 | 1 |

Observed surfaces (union of primary `surface` and `secondary_surfaces`):

```text
python  : build, ci, documentation, infrastructure, other, script, test, tooling
java    : backend, build, configuration, database, generated, migration, test, tooling
frontend: api, asset, build, frontend, generated, lockfile, other, test, tooling, vendor
```

Collective check against the plan's required list
(`backend, frontend, database, migration, test, fixture/configuration, infrastructure,
build, script, ci, documentation, asset, lockfile, generated, vendor`): all are
present. `fixture` is **not** present inside the copied target repositories, so it
is recorded as the host role of `skills/replicate-learning/tests/fixtures/` rather
than faked into a target fixture. This is asserted, not just documented
(`test_families_collectively_expose_the_required_surfaces`).

The frontend fixture is the only one with a deliberate `UNKNOWN`
(`mystery.odd`); the other two families have `unknown_count = 0`
(`test_only_the_frontend_fixture_has_a_deliberate_unknown`).

### 5.1 Worktree and git-tree snapshot coverage

`test_git_tracked_count_equals_both_artifact_sets` runs both snapshot kinds for all
three families and asserts, for each: `git ls-files -z` count equals
`file_count`, equals `tracked_file_count`, equals the number of **unique** index
paths, equals the number of **unique** coverage paths, and the two path sets are
equal. Additional snapshot tests:

- worktree mode hashes edited bytes and reports `dirty = true`;
- git-tree mode keeps the committed bytes after the same local edit while `dirty` is still reported;
- worktree mode records the index `vcs_object_id` even when the payload differs;
- a deleted tracked file fails in worktree mode and the message suggests `--snapshot git-tree`;
- a gitlink payload hashes the ASCII object ID in both snapshot modes and is never traversed;
- symlink payload semantics are covered where the platform permits it (skipped on this Windows host, see §9).

### 5.2 Revision and dirty semantics

`discover_git_context` records the full 40-character `HEAD` commit ID. `dirty` is
computed from `git status --porcelain=v1 -z --untracked-files=no` and only tracks
changes below the analysed root: editing a file outside a nested analysis root
leaves it clean, editing inside it marks it dirty, and untracked files never do.

## 6. Unknown-resolution evidence

Library level (`test_deliberate_unknown_is_reported_and_then_resolved_once` and
`FixtureUnknownResolutionTests`):

| Stage | `unknown_count` | `mystery.odd` classification | `rule_id` | `teaching_status` | strict audit |
|---|---:|---|---|---|---|
| frontend fixture, no override | 1 | `UNKNOWN` (surface `other`) | `fallback:unknown` | `LOCATED` | `FAIL` (`UNKNOWN_REMAINS`) |
| frontend fixture + `overrides/frontend.json` | 0 | `CLASSIFIED` (surface `tooling`) | `override:exact-path` | `LOCATED` | `PASS` |

The override reason is preserved verbatim
(`Repository-owned generator input documented by the project.`), and the override
changes **exactly one** entry — `project_index` is byte-equal between the two runs
and only `mystery.odd` differs in `coverage.entries`
(`test_the_override_changes_nothing_else_in_the_frontend_fixture`). Without
`--require-complete` the same honest-unknown artifact reports `PARTIAL` and is
still a successful scan product.

## 7. Negative evidence

All of the following are asserted in `test_coverage_audit.py` by mutating exactly
one property and asserting the exact violation code:

| Negative case | Violation code |
|---|---|
| coverage entry missing | `PATH_SET_MISMATCH` |
| duplicate path | `DUPLICATE_PATH` |
| `file_count` disagrees with the array | `COUNT_MISMATCH` |
| `unknown_count` disagrees with the entries | `UNKNOWN_COUNT_MISMATCH` |
| project-index/coverage revision disagree | `REVISION_MISMATCH` |
| generated/vendor/ignored entry without a reason (3 subcases) | `REASON_MISSING` |
| v1.1 typing/rule fields removed, or unknown `rule_id` | `V11_FIELD_MISSING` |
| secondary surfaces unsorted / duplicated / repeating the primary | `SURFACE_INVALID` |
| artifact path escapes the analysis root | `UNSAFE_PATH` |
| worktree file deleted after the scan | `SNAPSHOT_FILE_MISSING` |
| worktree bytes edited to the same length | `SNAPSHOT_HASH_MISMATCH` |
| worktree file resized | `SNAPSHOT_SIZE_MISMATCH` |
| git-tree path/revision does not resolve | `GIT_OBJECT_MISSING` |
| recorded git-tree hash mutated | `SNAPSHOT_HASH_MISMATCH` |
| gitlink payload hash mutated | `SNAPSHOT_HASH_MISMATCH` |
| unknown remains under `--require-complete` | `UNKNOWN_REMAINS` |
| schema-invalid artifact | `SCHEMA_INVALID` (deeper access short-circuited) |
| two independent defects present | both codes reported (`COUNT_MISMATCH` + `REVISION_MISMATCH`) |

Override loader negatives (`test_repository_scan.py`, `CoverageOverrideTests`):
duplicate JSON object key, invalid UTF-8, unsupported `schema_version`, unexpected
top-level key, non-object `entries`, absolute path, `..` escape, backslash path,
untracked override path, empty reason, `COVERED`, `UNKNOWN`, missing entry field,
missing file, invalid JSON.

Failure-preserves-output evidence: `test_strict_completeness_fails_and_preserves_existing_outputs`
and `test_audit_failure_before_publication_preserves_previous_artifacts` assert the
previous `project-index.json` bytes are unchanged after a failed run;
`test_existing_output_is_replaced_but_unrelated_files_survive` asserts an unrelated
file in the destination directory survives a successful run.

## 8. Determinism evidence

- `test_repeated_fixed_time_scan_is_byte_identical`: two scans of the same repository with `generated_at="2026-09-22T00:00:00Z"` produce byte-identical `dumps_artifact` output for both artifacts.
- CLI level, real temporary committed fixture (frontend template, 11 tracked files, revision `935a2e3201ae363db0e7faeb6425a7f6bda7adf1`): the same `scan_repository.py` command re-run produced `coverage.json` SHA-256 `F9AFDCDA485B867B046B1E74189812EB127A1D42D99B06BD4611888038F9E4BE` both times → byte-identical.

## 9. Real CLI smoke scan (Task 8 Step 2)

Not a dogfood run: this is a temporary fixture repository created from the
committed frontend template.

```text
tracked_ls_files=11   revision=935a2e3201ae363db0e7faeb6425a7f6bda7adf1

A) python scripts/scan_repository.py --root <tmp>/frontend --out <tmp>/artifacts \
     --snapshot worktree --generated-at 2026-09-22T00:00:00Z
   → status=PARTIAL indexed_files=11 coverage_entries=11 unknown_files=1        exit 0

B) python scripts/scan_repository.py --root <tmp>/frontend --out <tmp>/artifacts \
     --snapshot worktree --overrides tests/fixtures/repository_scanner/overrides/frontend.json \
     --generated-at 2026-09-22T00:00:00Z --require-complete
   → status=PASS indexed_files=11 coverage_entries=11 unknown_files=0           exit 0

C) python scripts/validate_coverage.py --project-index <tmp>/artifacts/project-index.json \
     --coverage <tmp>/artifacts/coverage.json --root <tmp>/frontend --require-complete
   → status=PASS indexed_files=11 coverage_entries=11 unknown_files=0           exit 0

D) python scripts/validate_artifact.py <tmp>/artifacts/project-index.json <tmp>/artifacts/coverage.json
   → PASS … (project-index v1.1.0) / PASS … (coverage v1.1.0)                    exit 0

E) surfaces=api,asset,build,frontend,generated,lockfile,test,tooling,vendor
   mystery.odd → ('tooling', 'CLASSIFIED', 'override:exact-path')
```

## 10. Final verification matrix

Run from `skills/replicate-learning/` unless stated otherwise.

| # | Command | Exit | Observed |
|---|---|---:|---|
| 1 | `python scripts/validate_artifact.py <7 v1 fixtures>` | 0 | 7 `PASS`, 0 `FAIL` (old `1.0.0` fixtures still valid) |
| 2 | `python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts` | 0 | Ran **320** tests, OK (skipped=3); wall time 149.5 s and 190.0 s across two runs |
| 3 | `python scripts/skill_selfcheck.py` | 0 | 检查项 122，失败 0 → PASS; 28 tools, no orphans |
| 4 | `python scripts/v2_selfcheck.py` | 0 | V2 self-check: PASS (6 contract entries) |
| 5 | `python -m compileall -q scripts` | 0 | no output |
| 6 | `git diff --check` (repo root) | 0 | no whitespace errors |
| 7 | `git diff --stat` (repo root) | 0 | `README.md | 50 ++++-`, `SKILL.md | 20 ++`, 2 files changed, 65 insertions(+), 5 deletions(-) |
| 8 | `git status --short` (repo root) | 0 | see §12 |

Focused counts (each run green, exit 0):

| Module | Tests |
|---|---:|
| `test_artifact_contract.py` | 32 |
| `test_file_classification.py` | 29 |
| `test_repository_scan.py` | 46 |
| `test_coverage_audit.py` | 29 |
| `test_repository_scan_cli.py` | 19 |

Baseline was 190 tests / 25 contract tests; the suite is now 320 tests / 32 contract
tests. No existing test was deleted, weakened or skipped to obtain a green run.

## 11. Compatibility and security review

- Formal specification files (`PROJECT_DEEPDIVE_SPEC.md`, `ARCHITECTURE.md`, `REQUIREMENTS.md`, `VIBECODING_SPEC.md`, `QUALITY_GATES.md`, `ACCEPTANCE_CRITERIA.md`, `IMPLEMENTATION_PLAN.md`, `docs/project-deepdive/phase-2-design.md`, the Phase 2 plan) were **not** modified.
- Only `project-index` and `coverage` gained a v1 minor; the other five kinds still accept exactly `1.0.0`, and `1.1.0` is rejected for them (`test_scanner_minor_is_not_enabled_for_other_artifact_kinds`). Unlisted minors fail closed (`test_unlisted_v1_minor_versions_fail_closed`).
- `shell=True` is never used. The scanner only runs read-only Git commands (`rev-parse`, `status`, `ls-files`, `ls-tree`, `cat-file`); none of them executes a hook or target-repository code.
- Git output is consumed as NUL-delimited bytes and decoded as UTF-8; an undecodable tracked path fails closed rather than being mangled.
- Directory symlinks are never walked (the scanner never walks the filesystem); a symlink's payload, not its target's content, is hashed.
- Artifacts and CLI output contain metadata only: paths, sizes, SHA-256 values, media types and reasons. No file bodies and no environment values are written. A tracked `.env` is indexed as `IGNORED_WITH_REASON` with `teaching_status = NOT_APPLICABLE` and its content is never read into an artifact.
- Publication writes only the two named files through staged temporaries; unrelated destination files are untouched, and an output path that would overwrite the override input or a tracked repository file is refused.
- Concurrency: each invocation is a single-process scan; temporary files are per-invocation (`tempfile.NamedTemporaryFile` with a unique name) so two scans into one directory cannot collide on a partial file.

## 12. Changed files

Modified in this working tree by Phase 2 (tracked files):

```text
 M README.md
 M skills/replicate-learning/SKILL.md
```

Created by Phase 2 (untracked in this working tree):

```text
skills/replicate-learning/schemas/README.md
skills/replicate-learning/schemas/v1/project-index.schema.json
skills/replicate-learning/schemas/v1/coverage.schema.json
skills/replicate-learning/scripts/file_classification.py
skills/replicate-learning/scripts/repository_scan.py
skills/replicate-learning/scripts/coverage_audit.py
skills/replicate-learning/scripts/scan_repository.py
skills/replicate-learning/scripts/validate_coverage.py
skills/replicate-learning/scripts/test_file_classification.py
skills/replicate-learning/scripts/test_repository_scan.py
skills/replicate-learning/scripts/test_coverage_audit.py
skills/replicate-learning/scripts/test_repository_scan_cli.py
skills/replicate-learning/references/coverage-policy.md
skills/replicate-learning/tests/fixtures/repository_scanner/python/**            (7 files)
skills/replicate-learning/tests/fixtures/repository_scanner/java/**              (7 files)
skills/replicate-learning/tests/fixtures/repository_scanner/frontend/**          (11 files)
skills/replicate-learning/tests/fixtures/repository_scanner/overrides/frontend.json
docs/project-deepdive/phase-2-verification.md
docs/project-deepdive/phase-2-review.patch        (generated review packet, not product code — see below)
```

`skills/replicate-learning/schemas/` and `scripts/test_artifact_contract.py` were
already untracked Phase 1 work; Phase 2 modified two schema files, `schemas/README.md`
and the contract test inside that already-untracked directory.

### Out-of-scope file (declared, one)

`docs/project-deepdive/phase-2-review.patch` is a **generated review packet**, not
product code. The implementation prompt's OUTPUT CONTRACT asks for a
`git diff --binary --no-ext-diff` location, and because every Phase 2 source file is
new and therefore untracked (and staging is forbidden for this task), the packet
combines the tracked diff for the two modified files with full new-file diffs for
the other 41 Phase 2 files (43 files total). It contains no independent logic and can
be deleted without affecting any behavior or test.

No other file outside the plan's allowed scope (Task 1–8 file lists plus the prompt's
SCOPE list) was created or modified.

## 13. No commit / push / stage / remote mutation

- `git commit`: not run.
- `git push`: not run.
- `git add` / `git stage`: not run (working tree status still shows the Phase 2 files as untracked).
- `git remote`: not read, not modified. `git fetch`/`git pull`: not run.
- No file outside this workspace was written.

## 14. Known limitations (all inside the Phase 2 boundary)

1. **Symlinks are unverified on this host.** Creating symlinks requires a privilege this Windows session does not have, so the symlink payload test is platform-skipped (3 skips in the full suite: symlink, newline-in-path, non-UTF-8 path). The code paths exist and are written but their runtime evidence comes from a POSIX host.
2. **Newline and non-UTF-8 tracked paths are unverified on this host.** Git for Windows refuses to index newline paths, and Windows cannot create non-UTF-8 file names. NUL-safety is still evidenced directly by `NulSafetyTests.test_nul_split_keeps_spaces_unicode_and_newlines_in_one_path`.
3. **File typing is convention-based, not semantic.** A path with a known extension in an unrecognized directory is `CLASSIFIED` with surface `other` and rule `extension:known`; Phase 3 decides its architectural role. `src/main/<language>` is classified as `backend` by build-tool convention, and the reason field says so explicitly.
4. **`git-tree` mode reads each blob again during the audit**, so a full audit costs one `git cat-file` per non-gitlink file. Phase 2 favors provable snapshot integrity over audit speed.
5. **No recursive submodule traversal.** A gitlink entry is indexed and its object ID is hashed; the nested repository is deliberately not scanned.
6. **Page/Gradle `src/main/resources` is not treated as backend**, so `application.yml` is `configuration` without a `backend` secondary surface. Only language source roots (`src/main/<language>`) carry the production-source convention.
7. **`path:documentation` also covers standard repository documents** (`README*`, `CHANGELOG*`, `LICENSE*`, `NOTICE`, …) whose extension is empty or documentation-like, because no other rule in the fixed vocabulary fits a licence file.
8. **No CI workflow was added** to this repository, so the new tests run through the documented local commands only.
