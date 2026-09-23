# Project DeepDive artifact schemas

This directory contains the public contracts for persisted Project DeepDive intermediate artifacts.

## Version policy

- Schema directories are grouped by major version (`v1/`, later `v2/`).
- Every artifact declares a semantic `schema_version`.
- **Accepted v1 versions are not global.** All seven kinds accept exactly `1.0.0`. Only `project-index` and `coverage` additionally accept the compatible scanner minor `1.1.0`, because only the Phase 2 repository scanner emits them.
- Readers reject unknown kinds, malformed versions, unsupported major versions, and versions not named by the selected schema. An unlisted v1 minor such as `1.2.0` fails closed for every kind, and `1.1.0` fails closed for the five kinds the scanner does not produce.
- A compatible v1 minor may add optional fields only after the v1 schema and compatibility tests accept both old and new v1 artifacts. Removing or changing required fields needs a new major version and a migration path.
- Existing replicate-learning batch manifests and gate JSON remain under their existing contracts. They are not silently reinterpreted as these artifacts.

## Phase 1 artifact set

| Kind | Responsibility |
|---|---|
| `project-index` | repository identity and deterministic file inventory |
| `stack-profile` | evidence-backed technology detection |
| `evidence` | canonical E0–E6 evidence records |
| `coverage` | final file classification and coverage accounting |
| `knowledge-graph` | typed project entities and relationships |
| `curriculum` | beginner-first ordered learning units |
| `quality-report` | auditable gate results and honest overall status |

Phase 1 defines and validates these contracts. Repository scanning, static analysis, graph construction, curriculum compilation and auditing are later phases.

## Version 1.1.0 additions (Phase 2 repository scanner)

`project-index` and `coverage` gained a compatible minor in Phase 2. Legacy `1.0.0` artifacts stay valid and unchanged; only scanner output uses the new optional fields.

| Kind | Added in 1.1.0 | Where |
|---|---|---|
| `project-index` | `content_kind`, `media_type`, `extension`, `vcs_object_id` | each `files[]` entry |
| `coverage` | `classification_policy_version` | top level |
| `coverage` | `secondary_surfaces`, `rule_id` | each `entries[]` entry |

Payload semantics for the new fields:

- `content_kind` is one of `text`, `binary`, `symlink`, `gitlink`.
- `media_type` is derived from a versioned internal table plus a bounded byte sniff — never from the host MIME registry, so two machines classify the same tree identically.
- `extension` is the lowercase final suffix without the dot, or `""` when the name has none.
- `vcs_object_id` is the Git object ID from `git ls-files --stage` (worktree snapshot) or `git ls-tree -r` (git-tree snapshot).
- For a symlink, `bytes` and `sha256` describe the link-target payload. For a gitlink (submodule entry), they describe the ASCII Git object ID and `media_type` is `application/x-gitlink`; the nested repository is never traversed.
- `secondary_surfaces` lists other applicable surfaces, sorted and deduplicated, and never repeats the primary `surface`.
- `rule_id` is a stable built-in rule ID, or `override:exact-path` for a human exact-path override.

`reason` stays the human-readable explanation. Required reasons, sorted secondary surfaces and version-specific invariants are enforced by the semantic coverage audit (`scripts/coverage_audit.py`), not by JSON Schema conditionals.

## Paths, snapshots, and incomplete coverage

- `project.root` is the analysis root. File paths and evidence locator paths are relative to it and must not escape it.
- `project.snapshot_kind = "git-tree"` means file byte counts and SHA-256 values describe blobs from `repository_revision`; `"worktree"` means they describe bytes read from the analyzed checkout.
- `coverage` may temporarily contain `classification = "UNKNOWN"` while analysis is incomplete. G01 can pass only after `unknown_count` is zero and every entry has one of the five final classifications.
