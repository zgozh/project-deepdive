# Phase 2 repair handoff for a new Luna implementation session

> Status: implementation handoff, 2026-09-23. The current review verdict is `REPAIR_REQUIRED`. This file does not authorize committing, pushing, changing Git remotes or starting Phase 3.

## Why this handoff exists

The earlier [repair prompt](phase-2-repair-round-1.md) is a complete requirement ledger, but it asks one worker to handle several unrelated changes at once. This handoff splits that work into four reviewable slices. Use the review report as the source of confirmed failures; use the earlier prompt for details only when a slice needs them.

Phase 0–2 work has been saved as a **local WIP checkpoint** on `project-deepdive/phase2-repair`; this is not Phase 2 acceptance. Inspect `git status --short --untracked-files=all`, the checkpoint commit, and the actual source files rather than relying on a completion report. Preserve the existing worktree. In particular, do not modify `text.md` or stage `docs/project-deepdive/phase-2-review.patch`.

## Essential context to read first

1. `AGENTS.md` — local development contract.
2. `docs/project-deepdive/reviews/phase-2-review-2026-09-23.md` — observed defects and acceptance requirements.
3. This handoff — execution order and output contract.
4. The relevant modules and tests named in each slice.

Consult `docs/project-deepdive/phase-2-design.md` §§6, 8–10, 12–15 and `docs/project-deepdive/plans/phase-2-repository-scanner-and-coverage.md` Tasks 3, 5, 6 and 8 when making a contract decision. Do not reread unrelated handbook or Phase 3 material. If a code choice would change the published schema, CLI or exit semantics, stop and report the exact conflict before making that change.

## Working rules

- Use the existing Python 3.12 standard-library implementation and `unittest`; add no dependency.
- For each slice, write a regression test, run it and record the expected failure, implement the smallest repair, then run the focused tests. A test that fails for an import/path mistake is not valid red evidence.
- Do not delete, weaken or skip tests. Preserve the three existing platform skips and report them honestly.
- Avoid broad refactors and unrelated formatting. Keep public artifact versions and CLI names.
- Never execute code in the target repository. Git calls use argument arrays and read-only commands.
- Do not stage, commit, push, rename/change remotes, create a PR, or enter Phase 3. The Principal Engineer handles integration after acceptance.
- For the publication repair, require restoration after **handled** failures. Do not claim crash or power-loss atomicity.
- If a slice reveals a fundamental conflict, report it as `BLOCKED` with evidence. Ordinary engineering decisions are yours.

## Slice A — G01 must compare with Git

**Files:** `scripts/coverage_audit.py`, `scripts/repository_scan.py`, `scripts/test_coverage_audit.py`, `scripts/test_repository_scan.py`; update `references/coverage-policy.md` after the tests pass. Paths are relative to `skills/replicate-learning/`.

Write focused regressions for all of these cases:

1. A two-file repository is scanned; the same tracked file is removed from both artifacts and all declared counts are adjusted. Strict audit must fail and name the omitted path.
2. Worktree audit compares the artifact path set and recorded `vcs_object_id` values with the **current Git index**, and confirms `repository_revision` equals current `HEAD`.
3. Git-tree audit compares membership and object IDs with the **declared revision tree**. A staged add/delete must not change that tree's expected membership.
4. A v1.1 index entry with `tracked=false` fails. A v1.1 coverage artifact without or with a wrong `classification_policy_version` fails.

Use a stable violation code; extend the published violation vocabulary only if none of the current codes accurately describes the failure, and update tests and coverage policy together. Do not count agreement between the two JSON artifacts as proof that Git has no other tracked files. Keep v1.0 fixtures readable.

After green, run:

```powershell
python -m unittest scripts.test_coverage_audit scripts.test_repository_scan -v
```

## Slice B — regular worktree files stay within the analysis root

**Files:** `scripts/repository_scan.py`, `scripts/coverage_audit.py`, `scripts/test_repository_scan.py`, `scripts/test_coverage_audit.py`; update `references/coverage-policy.md` after the tests pass.

Tests should cover a Git regular-file entry replaced by a final symlink and by an ancestor symlink to a file outside the analysis root. Where Windows supports it, test a junction/reparse ancestor; otherwise record a platform skip. Test both scanner and auditor. A genuine Git symlink entry must still record its link-target bytes without opening the target.

Before reading a regular file, check the final entry and ancestors for symlink/reparse indirection and verify containment under the analysis root. Reuse one guard for scanner and auditor where practical. The guard must run before hashing any external payload. Explicitly document any remaining race between the guard and later file opening; do not claim a race-free guarantee without platform-specific proof.

After green, run:

```powershell
python -m unittest scripts.test_repository_scan scripts.test_coverage_audit -v
```

## Slice C — restore both outputs after a handled publication failure

**Files:** `scripts/scan_repository.py`, `scripts/test_repository_scan_cli.py`; update `references/coverage-policy.md` if it describes publication semantics.

Write a test that starts with two old artifact files and one unrelated file, injects an `OSError` on the **second final replacement**, and asserts both old byte strings and the unrelated file remain unchanged. Also test first-time publication where an old target did not exist, and a rollback failure that gets a distinct, honest error.

Stage both new payloads and preserve the previous final states before any final replacement. On a handled replacement error, restore every changed target and remove only targets created by this invocation. Clean only this invocation's temporary/backup files. Do not suppress a rollback error or describe the result as crash-atomic.

After green, run:

```powershell
python -m unittest scripts.test_repository_scan_cli -v
```

## Slice D — CI context, diagnostics and final verification

**Files:** `scripts/file_classification.py`, `scripts/validate_coverage.py`, `scripts/test_file_classification.py`, `scripts/test_repository_scan_cli.py`, `references/coverage-policy.md`, `docs/project-deepdive/phase-2-verification.md`.

Write tests proving `src/workflows/order.py` and `src/agent/workflows/research.py` have no `ci` primary or secondary surface, while `.github/workflows/ci.yml`, `.circleci/config.yml` and recognized root CI filenames remain CI. Restrict the `workflows` convention to a real CI parent such as `.github`.

Write a CLI test for malformed `project-index.json`; `validate_coverage.py` must name the project-index path and return the current `SCHEMA_INVALID`/exit-1 result. Coverage input errors must still name coverage.

Update `phase-2-verification.md` by appending observed repair red/green evidence. Preserve its original Phase 2 TDD deviations and platform limitations. Document any new platform skip and the handled-failure scope of publication.

Run the final matrix from `skills/replicate-learning/`:

```powershell
$phase2Fixtures = Get-ChildItem tests/fixtures/artifacts/v1/*.json | ForEach-Object FullName
python scripts/validate_artifact.py @phase2Fixtures
python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
python -m compileall -q scripts
```

Then from the repository root:

```powershell
git diff --check
git status --short --untracked-files=all
git diff --cached --name-only
git remote -v
```

If a final command fails, fix the underlying Phase 2 issue and rerun the affected focused check and the final matrix. Record observed exit codes and counts. Do not use previous logs as the final proof.

## Handoff result to return

Use this compact format:

```text
STATUS: READY_FOR_REVIEW | BLOCKED

SLICE A: red command/result; green command/result; changed files; authoritative Git cases proved
SLICE B: red/green; scanner and auditor containment cases; platform skips; remaining race limit
SLICE C: red/green; injected failure point; byte equality before/after; rollback-failure behavior
SLICE D: red/green; CI positive/negative cases; CLI attribution

FINAL CHECKS: each command, exit code, observed test/check counts
CHANGED FILES: every changed path; flag any path outside the four slices
GIT SAFETY: staged? committed? pushed? remote changed? (YES/NO each)
LIMITATIONS: remaining actual Phase 2 limits only
```

Stop after returning this packet. The Principal Engineer will inspect the actual files and independently reproduce the previously failing cases before deciding whether to commit and push.
