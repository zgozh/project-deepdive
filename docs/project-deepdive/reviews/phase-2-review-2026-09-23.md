# Phase 2 Principal Engineer Review — 2026-09-23

> Verdict: `REPAIR_REQUIRED`
>
> Commit/push status: paused; no Phase 2 commit or push is authorized by this review
>
> Base revision: `1463a06437fc903edec24722ecbb686a46d8f9da`

## 1. Review scope

Reviewed against:

- `AGENTS.md`;
- `docs/project-deepdive/phase-2-design.md`;
- `docs/project-deepdive/plans/phase-2-repository-scanner-and-coverage.md`;
- R-003, R-004, R-005 and R-044;
- G01 and G02;
- AC-01, AC-04 and AC-05.

The review inspected the actual worktree, including untracked implementation files. It did not rely on DS's completion statement or on `git diff` alone.

Excluded from product scope:

- `docs/project-deepdive/phase-2-review.patch` — generated review transport, not source of truth;
- `text.md` — pre-existing user file, unrelated to the implementation.

An additional reviewer was requested, but that worker hit its runtime usage limit before producing findings. No independent-review result is claimed from that attempt.

## 2. Verified strengths

- The implementation keeps the existing Skill boundary and separates pure classification, Git inventory, semantic audit and thin CLIs.
- Git subprocesses use argument arrays rather than `shell=True`.
- Worktree and git-tree enumeration are deliberately separated.
- Existing v1.0 artifacts remain readable while scanner output uses v1.1 only for project-index and coverage.
- Exact-path overrides reject malformed, unused, escaping and unsupported decisions.
- Phase 2 does not emit `COVERED`.
- Python, Java and frontend fixture families exist and are exercised without network access.
- The implementation report honestly records its two TDD deviations and three platform skips.
- Existing replicate-learning self-checks and regression tests remain green in the current worktree.

## 3. Findings

### Critical — G01 can report false completeness when both artifacts omit the same tracked file

**Files:**

- `skills/replicate-learning/scripts/coverage_audit.py:168`
- `skills/replicate-learning/scripts/coverage_audit.py:185`
- `skills/replicate-learning/scripts/coverage_audit.py:234`
- `skills/replicate-learning/scripts/coverage_audit.py:250`

The audit compares project-index paths with coverage paths and verifies only the files already listed by project-index. It never independently enumerates the actual Git index/declared tree and compares that authoritative tracked path set with project-index.

Therefore, removing the same tracked path from both artifacts, adjusting their counts, and running strict audit returns `PASS`.

Fresh reproduction:

```text
OMITTED_BOTH status=PASS violations=[] omitted=two.txt
```

This violates R-004, G01 and AC-04 and creates exactly the “false completeness” state Project DeepDive is intended to prevent.

Required repair:

- independently enumerate the authoritative tracked set for the artifact snapshot;
- compare it with project-index even if project-index and coverage agree with each other;
- for worktree snapshots, also verify the repository HEAD/revision and current index object IDs;
- for git-tree snapshots, enumerate the declared revision rather than current index/HEAD membership;
- add a regression test that removes one path from both artifacts and expects a stable failure;
- enforce v1.1 scanner invariants such as `classification_policy_version` and `tracked = true` rather than merely checking entry-local optional fields.

### Important — two-file publication can leave a mixed generation on a handled failure

**File:** `skills/replicate-learning/scripts/scan_repository.py:62`

Both new documents are staged, but final replacement is sequential and has no rollback. If replacing the second file fails, the first file is already new while the second remains old.

Fresh injected reproduction:

```text
PUBLISH_ERROR injected second replace failure
PUBLISH_STATE index=new-index coverage=old-coverage
```

This regresses the repository's established atomic-publication strength and violates Phase 2's no-partial-output Done When condition.

The repair target is rollback on a handled write/replace failure. Two separate files cannot be made crash-atomic with two independent `os.replace` calls; documentation must not claim protection against process termination or power loss unless a separate transactional design is implemented and tested.

Required repair:

- preserve or back up both previous final states before the first replacement;
- if any replacement fails, restore every already-replaced target and remove newly created finals that had no predecessor;
- leave unrelated files untouched;
- add a test that fails specifically on the second replacement and asserts both originals are byte-identical afterward;
- surface rollback failure explicitly rather than reporting ordinary publication failure.

### Important — ordinary business/Agent `workflows/` code is classified as CI

**Files:**

- `skills/replicate-learning/scripts/file_classification.py:434`
- `skills/replicate-learning/scripts/file_classification.py:554`
- `skills/replicate-learning/scripts/file_classification.py:633`

The global CI component set contains `workflows`, so any path containing that directory becomes primary surface `ci`. This is unsafe for a product whose main dogfood targets include Agent frameworks and business workflow systems.

Fresh reproduction:

```text
CoverageDecision(surface='ci', classification='CLASSIFIED',
                 reason='CI/CD pipeline definition.', rule_id='name:ci')
```

for `src/workflows/order.py`.

Required repair:

- treat `workflows` as CI only in an actual CI context such as `.github/workflows/`;
- keep `.circleci` and known exact CI filenames supported;
- add negative tests for `src/workflows/order.py` and an Agent/business workflow path;
- retain the positive `.github/workflows/ci.yml` test.

### Important — worktree scanning/audit can follow a replaced regular path outside the analysis root

**Files:**

- `skills/replicate-learning/scripts/repository_scan.py:196`
- `skills/replicate-learning/scripts/repository_scan.py:346`
- `skills/replicate-learning/scripts/coverage_audit.py:268`

For an index entry whose Git mode says regular file, the implementation uses normal `open()`/`Path.is_file()`. If the worktree file or one of its ancestor directories has been replaced by a symlink/junction after checkout, those calls can follow it outside the analysis root. The artifact would then contain the external payload's size/hash.

This conflicts with the Phase 2 security contract: do not read outside the analyzed root. Direct tracked symlinks are different—their link-target text may be recorded without following the target.

Required repair:

- before reading a regular worktree entry, reject a final symlink/type mismatch;
- reject symlink/reparse ancestors and any resolved path outside the analysis root;
- apply the same checks in scan and audit paths through one shared helper where practical;
- preserve legitimate Git symlink handling through `readlink` without following the target;
- add a POSIX symlink-ancestor test and, where possible, a Windows junction/reparse test; unsupported platform cases must remain explicit skips.

### Minor — schema CLI can attribute a malformed project-index to the coverage path

**File:** `skills/replicate-learning/scripts/validate_coverage.py:52`

Both loads share one `try`, and the error output always prints `args.coverage`. A malformed project-index is therefore reported against the wrong artifact path.

Required repair: load/report each artifact with its own label/path, while retaining exit code `1` and `SCHEMA_INVALID`.

### Minor — generated review patch should not be committed

**File:** `docs/project-deepdive/phase-2-review.patch`

This 262 KB derived file duplicates source and documentation. It is useful as a transient handoff but would become stale immediately after repair.

Required repair: leave it untracked/excluded from the eventual commit. Regenerate only as an external review aid if needed.

## 4. Fresh verification evidence

Executed from `skills/replicate-learning/` unless stated otherwise:

| Check | Result |
|---|---|
| Seven Phase 1 artifact fixtures | 7 PASS, exit 0 |
| Full unittest discovery | `Ran 320 tests in 104.909s`, `OK (skipped=3)`, exit 0 |
| `skill_selfcheck.py` | exit 0; 122 checks / 0 failures in the same review turn |
| `v2_selfcheck.py` | PASS, 6 contract entries, exit 0 |
| `compileall -q scripts` | exit 0 |
| Simultaneous artifact omission probe | reproduced false `PASS` |
| Second-replacement failure probe | reproduced mixed output generation |
| Business workflow classification probe | reproduced false `ci` classification |

Green regression tests demonstrate that existing behavior was not broadly broken; they do not satisfy G01 because the confirmed counterexample is not covered by those tests.

## 5. Acceptance conditions for repair round 1

Repair round 1 is reviewable when:

- the simultaneous-omission probe is a deterministic failure, not `PASS`;
- worktree and git-tree authoritative membership are both tested;
- a forged/stale worktree repository revision cannot pass;
- missing v1.1 policy fields and false `tracked` flags cannot pass semantic audit;
- second-target replacement failure restores both original files;
- `src/workflows/order.py` is not CI while `.github/workflows/ci.yml` remains CI;
- regular-file symlink/junction escape is rejected by scanner and auditor;
- malformed project-index error identifies project-index;
- focused tests and the full regression/self-check matrix pass;
- `phase-2-verification.md` is amended with the new red/green evidence;
- `phase-2-review.patch` and `text.md` remain outside the commit;
- no Phase 3 work is introduced.

## 6. Integration decision

`REPAIR_REQUIRED`. The current worktree must not be committed or pushed as an accepted Phase 2 implementation because G01 can produce a false pass and publication can leave cross-artifact generations inconsistent.
