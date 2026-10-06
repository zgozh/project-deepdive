# Coverage policy — Phase 2 repository scanner

This reference documents the exact deterministic behavior of the Project DeepDive
Phase 2 repository scanner. It describes only implemented behavior.

Commands:

```bash
python scripts/scan_repository.py --root <git-repo-or-subtree> --out <artifact-dir> [options]
python scripts/validate_coverage.py --project-index <project-index.json> --coverage <coverage.json> --root <analysed-root> [--require-complete]
```

## 1. What Phase 2 outputs

Two artifacts, both at schema version `1.1.0`:

| Artifact | Responsibility |
|---|---|
| `project-index.json` | repository identity, tracked-file inventory, deterministic typing |
| `coverage.json` | per-file surface, classification, teaching status and the rule that decided it |

Legacy `1.0.0` artifacts stay valid and unchanged. Only `project-index` and
`coverage` accept the `1.1.0` minor; the other five v1 kinds still accept exactly
`1.0.0`, and an unlisted minor such as `1.2.0` fails closed everywhere.

Phase 2 **never emits `COVERED`**. Inventory, typing and surface classification
are not proof that a file was taught, graphed or source-verified, so a file that
was recognized and classified is `CLASSIFIED`, not `COVERED`. `COVERED` remains
reserved for a later phase that can show the file was actually taught.

## 2. Snapshot semantics

| `--snapshot` | Enumeration | Payload that `bytes`/`sha256` describe |
|---|---|---|
| `worktree` (default) | `git ls-files --stage -z` on the current index | current tracked worktree bytes, so local edits are visible |
| `git-tree` | `git ls-tree -r -z <HEAD>` | the blobs of the recorded `HEAD` commit, so staged additions or deletions cannot leak in |

Rules that hold in both modes:

- every Git invocation uses an explicit argument array; `shell=True` is never used;
- paths come from NUL-delimited Git output, so spaces, Unicode and newlines cannot split a path;
- results are restricted to the analysed subtree and normalized to relative POSIX paths;
- absolute paths, `..` escapes, duplicates, backslashes and non-UTF-8 tracked paths fail closed;
- untracked files are ignored: the Phase 2 contract is tracked-repository coverage;
- before reading a regular worktree file, the scanner and auditor inspect the
  final entry and every parent for symlink/reparse indirection, require a regular
  file, and check resolved containment within the analysed root;
- target repository code, scripts, package managers and Git hooks are never executed;
- file bodies, environment variable values and secret-like content are never written to artifacts or logs.

`project.root` is always `"."`. `project.dirty` is `true` when the **tracked**
index or worktree has changes below the analysed root; untracked files do not
affect it. In worktree mode a tracked file that is missing from disk is an
actionable error that suggests `--snapshot git-tree`.

Special payloads:

- **symlink** (`120000`): `bytes`/`sha256` describe the link-target payload, not the target file's content;
- **gitlink** (`160000`, a submodule entry): `bytes`/`sha256` describe the ASCII Git object ID and `media_type` is `application/x-gitlink`; the nested repository is never traversed.

A Git symlink entry is read only with `readlink`; its target is never opened.
The path guard runs before a regular file is opened, but a filesystem change can
still race between the guard and the later open. Phase 2 does not claim a
race-free guarantee against a concurrent path replacement.

## 3. Deterministic file typing

Typing never consults the operating system MIME registry, because that would make
two machines disagree about the same commit. The order is:

1. Git mode decides `symlink` and `gitlink`;
2. a versioned exact-name map (`Dockerfile`, `Makefile`, `CMakeLists.txt`, …);
3. a versioned lowercase-extension map;
4. a bounded byte sniff (8 KiB) for `NUL` when the name is unknown;
5. `text/plain` or `application/octet-stream` fallback.

`extension` is the lowercase alphanumeric final suffix without the dot, or `""`
when the name has none or the suffix is not purely alphanumeric.

`classify_file` is a pure function: it performs no filesystem, Git or network I/O.

## 4. Rule precedence

Rules are evaluated in this fixed order:

1. explicit exact-path override;
2. tracked sensitive environment files (`.env`, but not `.env.example`) → `IGNORED_WITH_REASON`;
3. strong vendor-directory conventions → `VENDOR`;
4. strong generated-directory conventions → `GENERATED`;
5. exact filenames: CI, infrastructure, lockfile, build, configuration, environment example, tooling;
6. path-component conventions (most specific first): fixture, migration, test, documentation, script, CLI, MCP, database, asset, frontend, backend, API, domain, agent, tooling, configuration;
7. known-extension fallback with **no** architectural claim;
8. otherwise `UNKNOWN` with surface `other`.

Every matched rule contributes a surface; the highest-precedence match is the
primary `surface` and the remaining distinct surfaces become the sorted,
deduplicated `secondary_surfaces`. Tier 7 (`extension:known`) never contributes a
secondary surface, and the safety rule returns immediately so a `.env` under a
`config/` directory has no `configuration` secondary.

Two ordering decisions are deliberate and worth stating because the design
enumerates conventions rather than ranking them:

- `path:migration` is evaluated before generic `path:database`/configuration rules, as the design states explicitly. A migration therefore has primary surface `migration` and secondary surface `database`.
- `path:fixture` is more specific than the generic `path:test` convention, so `tests/fixtures/user.json` is a `fixture` with secondary `test`. The design's own migration-before-database note shows the enumeration is not a precedence list.

Known source extensions without enough architectural context stay honest:
`src/sample/service.py` is `CLASSIFIED` with surface `other` and rule
`extension:known`. It is not mislabeled as `backend` merely because it contains
Python. A Maven/Gradle production source root (`src/main/<language>`) is matched
by `path:backend`, and its reason states that Phase 3 confirms the role.

The generic `workflows` directory name does not imply CI: that convention is
limited to root `.github/workflows/`. `.circleci/` and the recognized exact CI
filenames remain CI surfaces, so business or Agent workflow code keeps its
domain surface.

## 5. Stable rule IDs

| Rule ID | Meaning |
|---|---|
| `override:exact-path` | explicit exact tracked-path decision |
| `safety:tracked-env` | tracked environment file whose contents must not be taught or logged |
| `path:vendor` | strong vendored/third-party directory convention |
| `path:generated` | strong generated-output directory convention |
| `name:ci` | CI workflow/config filename or CI pipeline convention |
| `name:infrastructure` | Docker/Compose/Kubernetes/Nginx filename or deployment convention |
| `name:build` | build or package manifest filename |
| `name:lockfile` | dependency lockfile filename |
| `name:configuration` | exact application/configuration filename |
| `name:environment` | non-secret environment example filename |
| `name:tooling` | lint/format/typecheck/developer-tool filename |
| `path:test` | test directory or test filename convention |
| `path:fixture` | fixture directory convention |
| `path:configuration` | configuration directory convention |
| `path:tooling` | developer-tooling directory convention |
| `path:documentation` | documentation directory or repository-document name convention |
| `path:script` | script/automation directory convention |
| `path:cli` | CLI directory/entry convention |
| `path:mcp` | MCP/tool-server convention |
| `path:database` | database/schema/seed convention |
| `path:migration` | migration convention |
| `path:asset` | static/public asset convention |
| `path:frontend` | frontend/page/component/state convention |
| `path:backend` | explicit backend/server convention, or a build-tool production source root |
| `path:api` | explicit API/route convention |
| `path:domain` | explicit domain/core convention |
| `path:agent` | explicit agent/RAG/tool convention |
| `extension:known` | recognized file type with no reliable architectural role |
| `fallback:unknown` | no reliable deterministic rule matched |

Rule IDs are derived from this closed vocabulary, never from path text.

## 6. Status vocabulary

| Situation | `classification` | `teaching_status` |
|---|---|---|
| known owned file | `CLASSIFIED` | `LOCATED` |
| generated output | `GENERATED` | `NOT_APPLICABLE` |
| vendored third-party code | `VENDOR` | `NOT_APPLICABLE` |
| sensitive or explicitly ignored file | `IGNORED_WITH_REASON` | `NOT_APPLICABLE` |
| ambiguous file | `UNKNOWN` | `LOCATED` |

## 7. Exact-path overrides

An optional UTF-8 JSON file resolves ambiguity with recorded evidence:

```json
{
  "schema_version": "1.0.0",
  "entries": {
    "mystery.odd": {
      "surface": "tooling",
      "classification": "CLASSIFIED",
      "reason": "Repository-owned generator input documented by the project."
    }
  }
}
```

An override fails closed when it is:

- invalid UTF-8, invalid JSON, or contains a duplicate object key;
- using an unsupported `schema_version` (only `1.0.0` is accepted);
- carrying an unexpected top-level or entry key, or missing `surface`/`classification`/`reason`;
- using a path that is absolute, escaping, non-normalized, or not tracked in this snapshot;
- using an empty `reason`, an unknown surface, or a classification outside `CLASSIFIED`, `GENERATED`, `VENDOR`, `IGNORED_WITH_REASON`.

`COVERED` and `UNKNOWN` cannot be asserted by an override. There is no
"everything else is `other`" escape hatch: `UNKNOWN` can only be removed by an
exact path with a non-empty reason.

## 8. Audit

`scripts/coverage_audit.py` is the reusable G01 check and is called by both CLIs.
It verifies the schema, counts, revision, timestamp, path-set equality, path
safety, v1.1 fields, rule IDs, secondary-surface ordering, required reasons,
`UNKNOWN` count, worktree file presence/size/hash, git-tree object resolution and
gitlink payload semantics. Every mismatch is a failure, never a warning.

The audit independently checks project-index membership against Git. A
`worktree` snapshot is compared with the current index listed by
`git ls-files --stage -z`; its recorded revision must also equal current `HEAD`, and every v1.1
`vcs_object_id` must match the current index entry. A `git-tree` snapshot is
compared with the tree at its declared `repository_revision`, so later staged
additions or deletions do not change that snapshot's expected paths or object
IDs. For v1.1 artifacts, every project-index entry must say `tracked: true`,
and `classification_policy_version` must be present and equal the supported
policy version. The audit also checks `content_kind` against the authoritative
Git mode: symlink mode requires `symlink`, gitlink mode requires `gitlink`, and a
regular-file mode cannot claim either special kind. A `git-tree` artifact's
`repository_revision` must identify a full commit object, not a tree or blob.

| Status | Condition |
|---|---|
| `PASS` | no violations and zero `UNKNOWN` |
| `PARTIAL` | no violations, `UNKNOWN` files remain, completeness was not required |
| `FAIL` | any violation, including `UNKNOWN_REMAINS` under `--require-complete` |

Violation codes: `SCHEMA_INVALID`, `REVISION_MISMATCH`, `TIMESTAMP_MISMATCH`,
`COUNT_MISMATCH`, `DUPLICATE_PATH`, `PATH_SET_MISMATCH`, `UNSAFE_PATH`,
`V11_FIELD_MISSING`, `SURFACE_INVALID`, `REASON_MISSING`,
`UNKNOWN_COUNT_MISMATCH`, `UNKNOWN_REMAINS`, `SNAPSHOT_FILE_MISSING`,
`SNAPSHOT_PATH_UNSAFE`, `SNAPSHOT_SIZE_MISMATCH`, `SNAPSHOT_HASH_MISMATCH`,
`GIT_OBJECT_MISSING`,
`VCS_OBJECT_MISMATCH`, `TRACKED_FLAG_INVALID`, `CLASSIFICATION_POLICY_MISMATCH`,
`CONTENT_KIND_MISMATCH`.

## 9. Publication and exit codes

`scan_repository.py` builds, canonicalizes and audits both documents in memory
first, stages both payloads inside `--out`, preserves existing final states, and
then replaces `project-index.json` and `coverage.json`. If a handled write or
replacement error occurs, it restores each target whose replacement completed,
using its pre-publish backup or removing it when it had no predecessor. A target
whose replacement failed is excluded from rollback. Unrelated files in the
destination are never deleted, and an output path that would overwrite the
override input or a tracked repository file is refused. Re-running with the same
snapshot and `--generated-at` is byte-identical.

Rollback ownership includes only final paths whose `os.replace` call returned
successfully. A path whose replacement raised is excluded from rollback, so a
same-name file created by another writer after the backup check is left alone.
There is no interprocess lock or compare-and-swap: a concurrent same-name write
can still be overwritten by a successful `os.replace`, and a later rollback of
that successful replacement can restore the earlier backup or remove the path,
overwriting a concurrent update. Publication is not a multi-writer transaction.

The rollback guarantee covers handled failures while the process is running. Two
independent final-file replacements are not crash-atomic: process termination or
power loss between replacements can leave a mixed generation. If rollback itself
fails, the CLI reports that separately and retains the affected original backup
for recovery.

| Exit code | `scan_repository.py` | `validate_coverage.py` |
|---|---|---|
| `0` | scan and audit succeeded (`PARTIAL` allowed without `--require-complete`) | status is `PASS` or `PARTIAL` |
| `1` | artifact, audit or completeness failure | status is `FAIL` |
| `2` | usage or operational error (non-Git root, missing HEAD, unreadable file, invalid override) | usage or operational error |

Operational errors are reported on stderr without a traceback. The CLIs contain
no classification rules: they parse arguments, call the library, format results
and map exceptions to exit codes.
