# Phase 2 Repair Round 1 Prompt

> Give the following prompt to the implementation model in the existing worktree. It is a repair task, not a redesign and not Phase 3.

```text
You are the Phase 2 Repair Worker in the existing Project DeepDive worktree.

GOAL

Repair every Critical/Important finding and the two bounded Minor findings in:

docs/project-deepdive/reviews/phase-2-review-2026-09-23.md

The repaired Phase 2 must prevent false G01 completeness, preserve both old artifacts on a mid-publication failure, avoid treating business workflows as CI, and never read regular worktree payloads outside the analysis root.

CONTEXT

Read completely, in order:

1. AGENTS.md
2. docs/project-deepdive/reviews/phase-2-review-2026-09-23.md
3. docs/project-deepdive/phase-2-design.md
4. docs/project-deepdive/plans/phase-2-repository-scanner-and-coverage.md
5. docs/project-deepdive/phase-2-verification.md
6. skills/replicate-learning/scripts/coverage_audit.py
7. skills/replicate-learning/scripts/repository_scan.py
8. skills/replicate-learning/scripts/scan_repository.py
9. skills/replicate-learning/scripts/file_classification.py
10. their four Phase 2 test modules

The current tree contains uncommitted user and Phase 0/1/2 work. Preserve it. Do not rebuild the feature from scratch.

CONSTRAINTS

- Use TDD for every repair: add the smallest failing regression, run it and capture the expected failure, then implement the repair and rerun it.
- Do not change formal root specifications or Phase 2 design/plan.
- Do not add dependencies.
- Do not execute target repository code or use shell=True.
- Do not weaken, delete or skip existing tests.
- Do not enter Phase 3.
- Do not commit, stage, push or change remotes.
- Do not edit or commit text.md.
- Do not commit docs/project-deepdive/phase-2-review.patch; it is a stale generated handoff after any repair.
- Keep existing public CLI names, artifact versions and exit-code meanings.
- If a review requirement conflicts with actual code/spec, stop and report exact evidence instead of silently changing the contract.

SCOPE

Primary allowed implementation files:

- skills/replicate-learning/scripts/coverage_audit.py
- skills/replicate-learning/scripts/repository_scan.py
- skills/replicate-learning/scripts/scan_repository.py
- skills/replicate-learning/scripts/validate_coverage.py
- skills/replicate-learning/scripts/file_classification.py
- skills/replicate-learning/scripts/test_coverage_audit.py
- skills/replicate-learning/scripts/test_repository_scan.py
- skills/replicate-learning/scripts/test_repository_scan_cli.py
- skills/replicate-learning/scripts/test_file_classification.py
- skills/replicate-learning/references/coverage-policy.md
- docs/project-deepdive/phase-2-verification.md

Any other product-file change requires an explicit explanation in the output.

REPAIR 1 — AUTHORITATIVE GIT MEMBERSHIP

Add a failing test that:

1. creates a Git repository with at least two tracked files;
2. scans it;
3. removes the same file from project-index and coverage;
4. adjusts both declared counts and unknown_count;
5. runs audit with require_complete=True;
6. asserts audit fails with a stable path-set/membership violation naming the omitted file.

Then make audit independently enumerate the authoritative tracked set:

- worktree snapshot membership comes from the current Git index;
- git-tree membership comes from the artifact's declared revision tree, not the current index;
- worktree audit verifies the artifact revision matches current HEAD;
- scanner v1.1 project-index entries must have tracked=true;
- scanner v1.1 coverage must have classification_policy_version and it must equal the supported policy version;
- current index/tree object IDs must agree with recorded vcs_object_id.

Do not trust agreement between the two artifacts as proof that Git contains no omitted file.

REPAIR 2 — TWO-FILE PUBLICATION ROLLBACK

Add a failing unit test around publication that starts with two existing artifact files, injects failure specifically on the second final replacement, then asserts both files retain their original bytes and unrelated files remain unchanged.

Implement rollback-safe publication:

- prepare both new files before changing a final target;
- preserve both previous final states;
- on any replacement failure, restore every target already changed;
- remove a newly created target when it had no predecessor;
- clean only temporary/backup files created by this invocation;
- if rollback itself fails, raise/report a distinct fatal message identifying potentially mixed output.

Do not describe two sequential os.replace calls as atomic without this rollback behavior.

REPAIR 3 — CI CONTEXT

Add failing negative tests for:

- src/workflows/order.py
- src/agent/workflows/research.py

Neither may have primary or secondary surface ci merely because a component is named workflows.

Keep positive tests for:

- .github/workflows/ci.yml
- .circleci/config.yml
- recognized exact root CI filenames.

Restrict workflows-based CI detection to an actual CI parent context such as .github/workflows.

REPAIR 4 — WORKTREE CONTAINMENT

Add regression tests proving that a Git-regular entry cannot be read when:

- the final worktree path has been replaced by a symlink;
- an ancestor directory has been replaced by a symlink/junction to outside the analysis root.

On unsupported platforms, keep explicit skips, but implement and exercise the POSIX path. If a Windows junction test can run without elevated privileges, include it.

Use one shared containment/type guard from scanner and auditor where practical. Requirements:

- regular index entries must resolve inside analysis_root;
- regular index entries and ancestors must not traverse symlink/reparse indirection;
- Git symlink entries continue to use readlink and never follow their target;
- violations fail closed with actionable path text;
- no external payload is hashed before the guard passes.

REPAIR 5 — DIAGNOSTIC ACCURACY

Make validate_coverage.py report whether project-index or coverage failed schema loading, with the correct input path. Add a CLI regression for malformed project-index.

DOCUMENTATION

Update coverage-policy.md and phase-2-verification.md only after implementation. Preserve the original DS TDD deviations; append this repair round's actual red/green commands and exact results. Correct any publication wording that overstates atomicity.

VERIFICATION

Run focused tests for every repair, then from skills/replicate-learning run:

$fixtures = Get-ChildItem tests/fixtures/artifacts/v1/*.json | ForEach-Object FullName
python scripts/validate_artifact.py @fixtures
python -W ignore::ResourceWarning -m unittest discover -s scripts -p "test_*.py" -t scripts
python scripts/skill_selfcheck.py
python scripts/v2_selfcheck.py
python -m compileall -q scripts

From repository root run:

git diff --check
git status --short --untracked-files=all

DONE WHEN

- all five repair groups have observed red then green evidence;
- simultaneous omission cannot pass;
- both snapshot kinds compare against authoritative Git membership;
- mid-publication second replacement failure restores the old pair;
- business workflows are not CI;
- regular-file symlink/junction escape is rejected before reading;
- malformed project-index is attributed correctly;
- full regression and self-check matrix is green;
- review.patch and text.md remain untracked and unchanged;
- no commit/push/stage/remote mutation occurred.

OUTPUT CONTRACT

Return:

1. STATUS: READY_FOR_REVIEW or BLOCKED.
2. FINDING MATRIX: each review finding → changed files → tests → result.
3. TDD EVIDENCE: exact red command/result and green command/result for each repair.
4. AUTHORITATIVE MEMBERSHIP EVIDENCE: worktree omission, git-tree omission, stale revision, false tracked flag and missing policy version.
5. PUBLICATION FAILURE EVIDENCE: injected failure point and before/after hashes for both outputs.
6. CONTAINMENT EVIDENCE: final symlink and ancestor symlink/junction results by platform.
7. FINAL VERIFICATION: command, exit code and observed count for every command.
8. CHANGED FILES: every path and responsibility; explicitly list any out-of-scope path.
9. GIT SAFETY: staged/committed/pushed/remote changed — each YES/NO.
10. KNOWN LIMITATIONS: only real remaining Phase 2 limits.

Stop after returning this packet. Do not enter Phase 3.
```
