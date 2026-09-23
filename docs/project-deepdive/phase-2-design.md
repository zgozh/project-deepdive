# Project DeepDive Phase 2 Design — Repository Scanner & Coverage

> Status: implementation-ready design; no product code is implemented by this document
>
> Date: 2026-09-22
>
> Inputs: `PROJECT_DEEPDIVE_SPEC.md`, `ARCHITECTURE.md`, `REQUIREMENTS.md`, `QUALITY_GATES.md`, `ACCEPTANCE_CRITERIA.md`, `IMPLEMENTATION_PLAN.md`, and Phase 0/1 artifacts

## 1. Outcome

Phase 2 adds one deterministic vertical slice:

```text
Git repository + optional exact-path coverage overrides
→ tracked-file inventory
→ file metadata/type detection
→ repository-surface classification
→ project-index.json + coverage.json
→ cross-artifact and G01 audit
```

The slice is complete only when the two emitted artifacts validate, contain the same tracked path set, can expose unresolved files honestly, and can reach final `UNKNOWN = 0` through explicit classifications rather than invented certainty.

## 2. Entry gate

Before implementation, the worker must run the existing Phase 1 focused and full checks. Phase 2 may proceed only if:

- the seven v1 artifact schemas are present;
- `artifact_contract.py` validates the Phase 1 fixtures;
- all pre-existing tests remain green;
- the worker records, but does not overwrite, unrelated dirty-worktree changes.

If this gate fails, the worker reports the exact failure and stops. It must not rebuild Phase 1 from memory.

## 3. Scope

Phase 2 owns:

- locating the Git worktree and analysis root;
- enumerating every tracked path below the analysis root with NUL-safe Git commands;
- recording revision, tracked-dirty state, snapshot mode, sizes, hashes and deterministic file types;
- identifying primary and secondary repository surfaces;
- strongly identifying likely generated, vendored and sensitive ignored-with-reason files;
- representing ambiguous files as `UNKNOWN`;
- loading exact-path human overrides that resolve ambiguity with a reason;
- emitting canonical `project-index.json` and `coverage.json`;
- checking schema validity, count integrity, path-set equality, reasons, revision consistency and snapshot integrity;
- exercising Python-style, Java-style and frontend-containing fixture repositories.

Phase 2 does not own:

- AST, symbol, import, dependency, route or call-graph extraction;
- language/framework confidence or `stack-profile.json` generation;
- runtime command discovery or execution;
- business interpretation;
- knowledge graph, curriculum or handbook generation;
- prose teaching depth;
- non-Git filesystem inventory;
- semantic monorepo/workspace boundary inference; Phase 2 indexes and classifies the relevant manifests, while Phase 3 interprets their stack/workspace meaning;
- recursive scanning of submodule contents.

The exclusions belong to later phases. A Git submodule entry itself is still indexed and classified; its nested repository is not traversed.

## 4. Architecture options

### Option A — one scanner script

One CLI performs Git discovery, hashing, classification, serialization and auditing.

Advantages: few files and fast initial coding.

Disadvantages: rules become hard to test independently; the audit cannot be reused by Phase 9; Git failures and classification uncertainty become entangled.

### Option B — focused deterministic modules (selected)

Use separate modules for Git inventory, pure classification, semantic coverage audit and thin CLIs.

Advantages: each boundary has a direct test surface; later phases can reuse inventory and audit; classification rules can evolve without changing Git I/O; no empty framework is introduced.

Disadvantages: several small files instead of one.

### Option C — adapter/plugin framework now

Introduce generic repository, VCS, language and framework adapters in Phase 2.

Advantages: closer to the final architecture diagram.

Disadvantages: Phase 3 is the first phase that needs analyzers/adapters, so this creates abstractions without a second implementation and violates the no-architecture-theater rule.

Decision: Option B. Phase 2 creates only modules with live CLI or audit callers and tests. Adapter abstractions remain Phase 3 work.

## 5. Artifact compatibility decision

### 5.1 Why a compatible minor version is necessary

The current `project-index` v1.0 file entry records only `path`, `bytes`, `sha256` and `tracked`. That cannot persist Phase 2's required file-type result. The current `coverage` entry records a primary surface but not the rule that produced it or additional applicable surfaces.

Phase 2 therefore introduces schema version `1.1.0` for only `project-index` and `coverage`:

- existing `1.0.0` fixtures remain unchanged and valid;
- the two v1 schemas accept both `1.0.0` and `1.1.0`;
- only `1.1.0` scanner outputs use the new optional fields;
- other artifact kinds remain exactly `1.0.0`;
- `1.2.0`, other unlisted minor versions and all unsupported majors fail closed.

This follows the compatibility policy already documented in `schemas/README.md`: a compatible v1 minor may add optional fields when tests prove old and new artifacts both remain readable.

### 5.2 `project-index` v1.1 additions

Each scanner-produced file entry adds:

| Field | Meaning |
|---|---|
| `content_kind` | `text`, `binary`, `symlink`, or `gitlink` |
| `media_type` | deterministic internal media type, never host-registry dependent |
| `extension` | lowercase final suffix without `.`, or an empty string |
| `vcs_object_id` | Git object ID reported by `git ls-files --stage` |

`bytes` and `sha256` retain deterministic payload semantics:

- regular file: bytes and SHA-256 of the selected snapshot's file content;
- symlink: bytes and SHA-256 of the link-target payload;
- gitlink: bytes and SHA-256 of the ASCII Git object ID; `media_type` is `application/x-gitlink`.

The gitlink rule avoids pretending that submodule source was read while still giving the tracked entry a reproducible payload.

### 5.3 `coverage` v1.1 additions

The artifact adds optional `classification_policy_version`. Each scanner-produced entry adds:

| Field | Meaning |
|---|---|
| `secondary_surfaces` | other applicable surfaces, sorted and deduplicated |
| `rule_id` | stable built-in rule ID or `override:exact-path` |

The existing `reason` field remains the human-readable explanation. Semantic audit, rather than JSON Schema conditionals, enforces required reasons and version-specific invariants.

## 6. Snapshot and Git semantics

The scanner accepts a repository root and supports two explicit snapshot modes:

- `worktree` (default): enumerate tracked paths from Git and hash current tracked worktree bytes. It reflects local edits. A deleted tracked file is an actionable error; the scanner suggests `git-tree` if the committed snapshot is desired.
- `git-tree`: enumerate and read objects from the recorded `HEAD` commit. Local modifications do not change hashes, but `project.dirty` still reports tracked changes below the analysis root.

Rules:

- use subprocess argument arrays; never `shell=True`;
- in `worktree` mode enumerate the current index with `git ls-files --stage -z`;
- in `git-tree` mode enumerate the declared commit with `git ls-tree -r -z`, so staged additions/deletions cannot leak into the committed snapshot;
- use NUL-delimited Git output so spaces, Unicode and newlines in paths are not split;
- resolve the repository top-level and then restrict results to the requested analysis subtree;
- normalize artifact paths to relative POSIX form;
- reject absolute paths, `..` escapes, duplicates and stage-conflict entries;
- ignore untracked files because the Phase 2 contract is tracked-repository coverage;
- compute hashes in streaming mode for worktree regular files;
- never execute target-repository code;
- never place file contents in artifacts or normal logs.

`repository_revision` is the full `HEAD` commit ID. `project.root` is `.` relative to the analyzed root. `project.dirty` means tracked index/worktree changes exist below that root; untracked files do not affect it.

In worktree mode, `vcs_object_id` identifies the current index entry while `bytes`/`sha256` identify the current worktree payload; they may legitimately differ after an unstaged edit. In git-tree mode, both metadata sources refer to the declared commit tree.

Non-Git inventory is deferred because the current coverage contract is explicitly tracked-file based. A non-Git root must fail with a clear operational error rather than synthesize a revision.

## 7. Deterministic file typing

Do not use the operating system's MIME registry because results can differ between machines. File typing uses:

1. Git mode for symlink/gitlink;
2. a versioned exact-name map (`Dockerfile`, `Makefile`, known manifests and lockfiles);
3. a versioned lowercase-extension map;
4. a bounded byte sniff for NUL/binary detection when the name is unknown;
5. `text/plain` or `application/octet-stream` fallback.

The classifier may read a bounded prefix for typing, but artifacts contain only metadata and hashes.

## 8. Surface and coverage classification

Classification is a pure function over file facts plus optional exact-path overrides. Rule precedence is fixed:

1. exact-path override;
2. sensitive tracked environment files such as `.env` (not `.env.example`) → `IGNORED_WITH_REASON`;
3. strong vendor-directory conventions → `VENDOR`;
4. strong generated-directory/name conventions → `GENERATED`;
5. exact filenames for manifests, lockfiles, CI, build, infrastructure and tooling;
6. path conventions for tests, fixtures, docs, scripts, CLI, MCP, database, migrations, assets, frontend, backend, API, domain and agent code;
7. known extension fallback;
8. otherwise `UNKNOWN` with surface `other`.

The output status policy is intentionally conservative:

- known owned files → `CLASSIFIED`, `teaching_status = LOCATED`;
- generated files → `GENERATED`, `teaching_status = NOT_APPLICABLE`;
- vendored files → `VENDOR`, `teaching_status = NOT_APPLICABLE`;
- sensitive or explicitly ignored files → `IGNORED_WITH_REASON`, `teaching_status = NOT_APPLICABLE`;
- ambiguous files → `UNKNOWN`, `teaching_status = LOCATED`;
- Phase 2 never emits `COVERED`, because inventory is not proof that a file was taught, graphed or source-verified.

Known source extensions without enough architectural context are safely `CLASSIFIED` with surface `other` and a reason stating that Phase 3 will determine their role. They are not mislabeled as backend merely because they contain Python or Java.

Stable built-in rule IDs are:

| Rule ID | Purpose |
|---|---|
| `override:exact-path` | explicit exact tracked-path decision |
| `safety:tracked-env` | tracked environment file whose contents must not be taught or logged |
| `path:vendor` | strong vendored/third-party directory convention |
| `path:generated` | strong generated-output directory or filename convention |
| `name:ci` | exact CI workflow/config filename |
| `name:infrastructure` | Docker/Compose/Kubernetes/Nginx filename |
| `name:build` | build or package manifest filename |
| `name:lockfile` | dependency lockfile filename |
| `name:configuration` | exact application/configuration filename |
| `name:environment` | non-secret environment example filename |
| `name:tooling` | lint/format/typecheck/developer-tool filename |
| `path:test` | test directory or test filename convention |
| `path:fixture` | fixture directory convention |
| `path:configuration` | configuration directory convention |
| `path:tooling` | developer-tooling directory convention |
| `path:documentation` | documentation directory/name convention |
| `path:script` | script/automation directory convention |
| `path:cli` | CLI directory/entry convention |
| `path:mcp` | MCP/tool-server convention |
| `path:database` | database/schema/seed convention |
| `path:migration` | migration convention; ordered before generic database/config rules |
| `path:asset` | static/public asset convention |
| `path:frontend` | frontend/page/component/state/client convention |
| `path:backend` | explicit backend/server convention |
| `path:api` | explicit API/route convention |
| `path:domain` | explicit domain/core convention |
| `path:agent` | explicit agent/RAG/tool convention |
| `extension:known` | recognized file type with no reliable architectural role |
| `fallback:unknown` | no reliable deterministic rule matched |

Exact media-type outputs exercised by the contract tests are `text/x-python`, `text/x-dockerfile`, `image/png`, `text/plain`, `application/octet-stream`, and `application/x-gitlink`.

### Exact-path overrides

An optional UTF-8 JSON input has this shape:

```json
{
  "schema_version": "1.0.0",
  "entries": {
    "relative/path.odd": {
      "surface": "tooling",
      "classification": "CLASSIFIED",
      "reason": "Repository-owned generator input documented by the project."
    }
  }
}
```

Override paths must be normalized tracked paths and must exist in the index. Every override requires a non-empty reason. Phase 2 overrides may choose `CLASSIFIED`, `GENERATED`, `VENDOR`, or `IGNORED_WITH_REASON`; they may not assert `COVERED` or retain `UNKNOWN`. Unused, duplicate, escaping or malformed overrides fail closed.

## 9. Semantic coverage audit

Schema validation is necessary but insufficient. The reusable audit checks:

- both artifacts pass `artifact_contract.validate_artifact`;
- both artifacts use the same repository revision and generated timestamp;
- `file_count`, `tracked_file_count`, `unknown_count` and actual arrays agree;
- project-index paths and coverage paths are unique and exactly equal;
- all artifact paths are normalized, relative and non-escaping;
- every scanner-produced entry has the v1.1 fields and a known rule ID;
- secondary surfaces are sorted, unique and do not repeat the primary surface;
- every `GENERATED`, `VENDOR` and `IGNORED_WITH_REASON` entry has a reason;
- the declared unknown count equals the number of `UNKNOWN` entries;
- `--require-complete` rejects any `UNKNOWN` entry;
- worktree snapshots still have every indexed file and matching size/hash;
- git-tree snapshots resolve every indexed object at the declared revision;
- gitlink payload semantics are checked without traversing the submodule.

Cross-artifact or snapshot mismatches are failures, not warnings. Without `--require-complete`, a structurally correct artifact containing honest unknowns reports `PARTIAL` but is still a successful scan product. With the flag it fails G01.

## 10. CLI contracts

### Scan

```text
python scripts/scan_repository.py \
  --root <git-repository-or-subtree> \
  --out <artifact-directory> \
  [--snapshot worktree|git-tree] \
  [--overrides <coverage-overrides.json>] \
  [--generated-at YYYY-MM-DDTHH:MM:SSZ] \
  [--require-complete]
```

The command writes only `project-index.json` and `coverage.json`. It builds and validates both in memory, writes temporary files in the destination directory, then replaces final files. A pre-write error must leave existing outputs unchanged. Re-running with the same snapshot and `--generated-at` must be byte-identical.

Exit behavior:

- `0`: scan and audit succeeded; output may be `PARTIAL` only when `--require-complete` was not requested;
- `1`: artifact/audit/completeness failure;
- `2`: usage or operational error such as non-Git root, unreadable file or invalid override.

### Audit

```text
python scripts/validate_coverage.py \
  --project-index <project-index.json> \
  --coverage <coverage.json> \
  --root <analyzed-root> \
  [--require-complete]
```

Output is concise and machine-friendly: status, counts and one line per violation. It does not emit a Phase 9 `quality-report.json` early.

## 11. Fixture strategy

Three committed fixture templates are copied into temporary directories and initialized as isolated Git repositories during tests. Local Git user configuration and `core.autocrlf=false` are set in each temporary repository before committing.

- Python-style: `pyproject.toml`, package source, tests, script, CI, Docker and docs.
- Java-style: `pom.xml`, main/test Java, application config, migration, Maven wrapper metadata and a generated target artifact.
- Frontend-containing: `package.json`, lockfile, page/component/API client, Vite config, frontend test, static asset, vendored/generated paths and one deliberately unknown extension.

The frontend fixture is scanned once without an override to prove honest `UNKNOWN`, then with an exact-path override to prove G01 can reach zero. Tests assert meaningful surfaces, not merely total counts.

## 12. Failure behavior

The scanner fails closed for:

- non-Git roots or missing `HEAD`;
- unresolved merge-stage entries;
- tracked paths that cannot be represented as valid UTF-8 JSON strings;
- tracked paths that escape the analysis root;
- deleted/unreadable files in worktree mode;
- invalid UTF-8 override JSON, duplicate JSON keys or unused override paths;
- output paths that would overwrite either input override or target repository source file;
- schema validation errors;
- partial publication errors before final replacement.

Ambiguous file role is not an operational failure. It becomes `UNKNOWN` and is resolved through an evidence-bearing override.

## 13. Security and privacy

- Do not execute target scripts, package managers or hooks.
- Do not follow directory symlinks.
- Do not read outside the analyzed root in worktree mode.
- Do not log file bodies, environment values or secret-like content.
- A tracked `.env` remains indexed but is `IGNORED_WITH_REASON`; its content is never emitted.
- Commands use explicit argument arrays and bounded output handling.
- Output directories are created without deleting existing directories or unrelated files.

## 14. Acceptance mapping

| Contract | Phase 2 evidence |
|---|---|
| R-003 / AC-01 | full legacy regression suite remains green; existing commands unchanged |
| R-004 / AC-04 | Git path set equals index and coverage sets; final audit can require zero unknowns |
| R-005 / G02 | deterministic primary/secondary surfaces exercised by all three fixture families |
| R-044 | ambiguous file remains `UNKNOWN` until an explicit reasoned override exists |
| G01 | count/set/reason/completeness checks in semantic audit |
| AC-05 | CI, Docker, config, test, docs, build, script and asset fixture assertions |
| Phase 2 Done When | Git count parity, override resolution and missing-file detection tests |

## 15. Phase 2 Done When

Phase 2 is ready for architectural review only when all of the following are evidenced:

- old v1.0 artifact fixtures still validate;
- new scanner outputs use v1.1.0 and validate;
- Python, Java and frontend fixture repositories are scanned end to end;
- every Git-tracked fixture path appears exactly once in both artifacts;
- worktree and git-tree modes are tested;
- generated/vendor/ignored classifications have reasons;
- the deliberate unknown fails `--require-complete`;
- an exact-path override resolves it and produces `unknown_count = 0`;
- missing coverage entries and missing/mutated snapshot files are rejected;
- repeated fixed-time scans are byte-identical;
- CLI failure does not overwrite prior valid artifacts;
- focused tests, the complete unittest suite, both self-checks, compileall and `git diff --check` pass;
- implementation diff and command outputs are returned for independent review;
- no commit, push or remote mutation is performed by the implementation worker.
