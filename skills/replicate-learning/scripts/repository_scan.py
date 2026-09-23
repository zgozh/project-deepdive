#!/usr/bin/env python3
"""Deterministic Git-backed repository inventory for Project DeepDive Phase 2.

The module only ever runs read-only Git plumbing/front-end commands through
explicit argument arrays.  It never uses a shell, never executes target
repository code, scripts, package managers or hooks, and never logs file bodies.

Two snapshots are supported:

``worktree``
    Enumerate the current index with ``git ls-files --stage -z`` and hash the
    current worktree payload.  A deleted tracked file is an actionable error.

``git-tree``
    Enumerate the committed tree with ``git ls-tree -r -z`` and hash the blobs
    of that revision, so staged additions/deletions cannot leak into the
    snapshot.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from artifact_contract import validate_artifact
from file_classification import (
    CLASSIFICATION_POLICY_VERSION,
    CoverageOverride,
    FileFacts,
    classify_file,
    detect_media_type,
    extension_of,
    normalize_artifact_path,
    normalize_override,
)

SNAPSHOT_KINDS: tuple[str, ...] = ("worktree", "git-tree")
SCANNER_SCHEMA_VERSION = "1.1.0"
OVERRIDE_SCHEMA_VERSION = "1.0.0"
SAMPLE_BYTES = 8192
_CHUNK_BYTES = 65536
_MODE_SYMLINK = "120000"
_MODE_GITLINK = "160000"
_GENERATED_AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_OVERRIDE_TOP_LEVEL_KEYS = frozenset({"schema_version", "entries"})
_OVERRIDE_ENTRY_KEYS = frozenset({"surface", "classification", "reason"})


class RepositoryScanError(RuntimeError):
    """An operational failure that must stop the scan instead of guessing."""


@dataclass(frozen=True)
class GitContext:
    analysis_root: Path
    repository_root: Path
    analysis_prefix: str
    revision: str
    dirty: bool


@dataclass(frozen=True)
class IndexedFile:
    path: str
    byte_count: int
    sha256: str
    content_kind: str
    media_type: str
    extension: str
    vcs_object_id: str


@dataclass(frozen=True)
class ScanOptions:
    root: Path
    snapshot_kind: str
    generated_at: str
    overrides_path: Path | None = None


@dataclass(frozen=True)
class ScanArtifacts:
    project_index: dict[str, object]
    coverage: dict[str, object]


def split_nul(raw: bytes) -> tuple[bytes, ...]:
    """Split NUL-delimited Git output.  Paths are never split on whitespace."""
    return tuple(part for part in raw.split(b"\x00") if part)


def _run_git(cwd: Path, *args: str, input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            input=input_bytes,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise RepositoryScanError(f"cannot run Git in {cwd}: {exc}") from exc


def _decode_path(raw: bytes, context: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RepositoryScanError(
            f"tracked path is not valid UTF-8 and cannot be written to a JSON artifact: {exc}"
        ) from exc


def _under(path: str, prefix: str) -> bool:
    if not prefix:
        return True
    return path == prefix or path.startswith(prefix + "/")


def _strip_prefix(path: str, prefix: str) -> str:
    if not prefix:
        return path
    return path[len(prefix) + 1:]


def analysis_prefix_between(repository_root: Path, analysis_root: Path) -> str:
    """Return the POSIX prefix of ``analysis_root`` below ``repository_root``."""
    repository = Path(repository_root).resolve()
    root = Path(analysis_root).resolve()
    if root == repository:
        return ""
    if repository not in root.parents:
        raise RepositoryScanError(
            f"analysis root {root} is not inside the Git repository {repository}"
        )
    return root.relative_to(repository).as_posix()


def _is_dirty(repository_root: Path, prefix: str) -> bool:
    result = _run_git(
        repository_root, "status", "--porcelain=v1", "-z", "--untracked-files=no",
    )
    if result.returncode != 0:
        raise RepositoryScanError(
            "cannot read Git worktree status: "
            + result.stderr.decode("utf-8", "replace").strip()
        )
    entries = split_nul(result.stdout)
    index = 0
    while index < len(entries):
        entry = _decode_path(entries[index], "git status")
        status, _, path = entry[:2], entry[2:3], entry[3:]
        if status[:1] in ("R", "C") and index + 1 < len(entries):
            index += 1
            source = _decode_path(entries[index], "git status")
            if _under(source, prefix):
                return True
        if _under(path, prefix):
            return True
        index += 1
    return False


def discover_git_context(root: Path) -> GitContext:
    """Locate the Git worktree and analysis root, or fail with an operational error."""
    analysis_root = Path(root)
    top = _run_git(analysis_root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise RepositoryScanError(
            f"{analysis_root}: not inside a Git repository; Phase 2 requires a Git worktree"
        )
    repository_root = Path(os.fsdecode(top.stdout).strip())
    prefix = analysis_prefix_between(repository_root, analysis_root)

    head = _run_git(repository_root, "rev-parse", "HEAD")
    if head.returncode != 0:
        raise RepositoryScanError(
            f"{analysis_root}: Git HEAD is missing; commit at least one revision before scanning"
        )
    revision = head.stdout.decode("ascii").strip()

    return GitContext(
        analysis_root=Path(analysis_root).resolve(),
        repository_root=repository_root.resolve(),
        analysis_prefix=prefix,
        revision=revision,
        dirty=_is_dirty(repository_root, prefix),
    )


def _hash_worktree_file(path: Path, sample_bytes: int) -> tuple[int, str, bytes]:
    digest = hashlib.sha256()
    total = 0
    sample = bytearray()
    if not path.exists() and not path.is_symlink():
        raise RepositoryScanError(
            f"tracked file is missing from the worktree: {path}; "
            "re-run with --snapshot git-tree to hash the committed revision"
        )
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
                if len(sample) < sample_bytes:
                    sample += chunk[: sample_bytes - len(sample)]
    except OSError as exc:
        raise RepositoryScanError(f"cannot read tracked file {path}: {exc}") from exc
    return total, digest.hexdigest(), bytes(sample)


def git_capture(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Run one read-only Git command with an explicit argument array; never a shell."""
    return _run_git(Path(cwd), *args)


def hash_git_object(
    repository_root: Path,
    object_id: str,
    sample_bytes: int = SAMPLE_BYTES,
) -> tuple[int, str, bytes]:
    """Stream-hash one Git object and return ``(byte_count, sha256, sample)``."""
    process = subprocess.Popen(
        ["git", "cat-file", "blob", object_id],
        cwd=str(repository_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    digest = hashlib.sha256()
    total = 0
    sample = bytearray()
    try:
        while True:
            chunk = process.stdout.read(_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            if len(sample) < sample_bytes:
                sample += chunk[: sample_bytes - len(sample)]
        stderr = process.stderr.read()
    finally:
        process.stdout.close()
        process.stderr.close()
    if process.wait() != 0:
        raise RepositoryScanError(
            f"cannot read Git object {object_id}: " + stderr.decode("utf-8", "replace").strip()
        )
    return total, digest.hexdigest(), bytes(sample)


def _hash_git_blob(context: GitContext, object_id: str, sample_bytes: int) -> tuple[int, str, bytes]:
    return hash_git_object(context.repository_root, object_id, sample_bytes)


def _indexed_file(
    artifact_path: str,
    git_kind: str,
    object_id: str,
    byte_count: int,
    sha256: str,
    sample: bytes,
) -> IndexedFile:
    media_type, content_kind = detect_media_type(artifact_path, git_kind, sample)
    return IndexedFile(
        path=artifact_path,
        byte_count=byte_count,
        sha256=sha256,
        content_kind=content_kind,
        media_type=media_type,
        extension=extension_of(artifact_path),
        vcs_object_id=object_id,
    )


def _gitlink_file(artifact_path: str, object_id: str) -> IndexedFile:
    payload = object_id.encode("ascii")
    return _indexed_file(
        artifact_path,
        "gitlink",
        object_id,
        len(payload),
        hashlib.sha256(payload).hexdigest(),
        b"",
    )


def _artifact_path(raw_path: str, context: GitContext) -> str | None:
    if not _under(raw_path, context.analysis_prefix):
        return None
    try:
        return normalize_artifact_path(_strip_prefix(raw_path, context.analysis_prefix))
    except ValueError as exc:
        raise RepositoryScanError(
            f"tracked path is not a safe analysis-root-relative path: {raw_path!r}: {exc}"
        ) from exc


def _finalize(files: list[IndexedFile]) -> tuple[IndexedFile, ...]:
    files.sort(key=lambda item: item.path)
    for previous, current in zip(files, files[1:]):
        if previous.path == current.path:
            raise RepositoryScanError(f"duplicate tracked path in the index: {current.path}")
    return tuple(files)


def _enumerate_worktree(context: GitContext) -> tuple[IndexedFile, ...]:
    result = _run_git(context.repository_root, "ls-files", "--stage", "-z")
    if result.returncode != 0:
        raise RepositoryScanError(
            "cannot enumerate tracked files: " + result.stderr.decode("utf-8", "replace").strip()
        )

    files: list[IndexedFile] = []
    for record in split_nul(result.stdout):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.decode("ascii").split(" ")
        if not separator or len(fields) != 3:
            raise RepositoryScanError(
                f"unexpected git ls-files --stage record: {metadata!r}"
            )
        mode, object_id, stage = fields
        if stage != "0":
            raise RepositoryScanError(
                "unresolved merge stage "
                f"{stage} for tracked path {_decode_path(raw_path, 'git ls-files')!r}; "
                "resolve the conflict index before scanning"
            )
        raw = _decode_path(raw_path, "git ls-files")
        artifact_path = _artifact_path(raw, context)
        if artifact_path is None:
            continue

        if mode == _MODE_GITLINK:
            files.append(_gitlink_file(artifact_path, object_id))
            continue

        target = context.analysis_root / artifact_path
        if mode == _MODE_SYMLINK:
            try:
                link_target = os.readlink(target)
            except OSError as exc:
                raise RepositoryScanError(
                    f"cannot read tracked symlink {target}: {exc}; "
                    "re-run with --snapshot git-tree to hash the committed revision"
                ) from exc
            payload = os.fsencode(link_target)
            files.append(_indexed_file(
                artifact_path, "symlink", object_id, len(payload),
                hashlib.sha256(payload).hexdigest(), payload[:SAMPLE_BYTES],
            ))
            continue

        byte_count, sha256, sample = _hash_worktree_file(target, SAMPLE_BYTES)
        files.append(_indexed_file(artifact_path, "file", object_id, byte_count, sha256, sample))

    return _finalize(files)


def _enumerate_git_tree(context: GitContext) -> tuple[IndexedFile, ...]:
    result = _run_git(context.repository_root, "ls-tree", "-r", "-z", context.revision)
    if result.returncode != 0:
        raise RepositoryScanError(
            "cannot enumerate the committed tree: "
            + result.stderr.decode("utf-8", "replace").strip()
        )

    files: list[IndexedFile] = []
    for record in split_nul(result.stdout):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.decode("ascii").split(" ")
        if not separator or len(fields) < 3:
            raise RepositoryScanError(f"unexpected git ls-tree record: {metadata!r}")
        mode, object_type, object_id = fields[0], fields[1], fields[2]
        raw = _decode_path(raw_path, "git ls-tree")
        artifact_path = _artifact_path(raw, context)
        if artifact_path is None:
            continue

        if mode == _MODE_GITLINK or object_type == "commit":
            files.append(_gitlink_file(artifact_path, object_id))
            continue
        if object_type != "blob":
            raise RepositoryScanError(
                f"unsupported Git object type {object_type!r} for tracked path {raw!r}"
            )

        byte_count, sha256, sample = _hash_git_blob(context, object_id, SAMPLE_BYTES)
        git_kind = "symlink" if mode == _MODE_SYMLINK else "file"
        files.append(_indexed_file(artifact_path, git_kind, object_id, byte_count, sha256, sample))

    return _finalize(files)


def enumerate_indexed_files(
    context: GitContext,
    snapshot_kind: str,
) -> tuple[IndexedFile, ...]:
    """Enumerate every tracked path below the analysis root for one snapshot."""
    if snapshot_kind == "worktree":
        return _enumerate_worktree(context)
    if snapshot_kind == "git-tree":
        return _enumerate_git_tree(context)
    raise RepositoryScanError(
        f"unknown snapshot kind {snapshot_kind!r}: expected one of {', '.join(SNAPSHOT_KINDS)}"
    )


def _override_pairs_without_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RepositoryScanError(f"duplicate object key in the coverage override: {key}")
        result[key] = value
    return result


def _reject_override_constant(value: str) -> None:
    raise RepositoryScanError(f"invalid JSON constant in the coverage override: {value}")


def load_coverage_overrides(path: str | Path, tracked_paths) -> dict[str, CoverageOverride]:
    """Load strict exact-path overrides, failing closed on anything unused or malformed."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RepositoryScanError(f"coverage override file does not exist: {source}") from exc
    except UnicodeDecodeError as exc:
        raise RepositoryScanError(
            f"coverage override {source} is not valid UTF-8: {exc}"
        ) from exc
    except OSError as exc:
        raise RepositoryScanError(f"cannot read coverage override {source}: {exc}") from exc

    try:
        document = json.loads(
            text,
            object_pairs_hook=_override_pairs_without_duplicates,
            parse_constant=_reject_override_constant,
        )
    except RepositoryScanError:
        raise
    except json.JSONDecodeError as exc:
        raise RepositoryScanError(f"coverage override {source} is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise RepositoryScanError(f"coverage override {source} must be a JSON object")
    unexpected = sorted(set(document) - _OVERRIDE_TOP_LEVEL_KEYS)
    if unexpected:
        raise RepositoryScanError(
            f"coverage override {source} has unexpected key(s): {', '.join(unexpected)}"
        )
    version = document.get("schema_version")
    if version != OVERRIDE_SCHEMA_VERSION:
        raise RepositoryScanError(
            f"coverage override {source}: unsupported schema_version {version!r}; "
            f"expected {OVERRIDE_SCHEMA_VERSION!r}"
        )
    entries = document.get("entries")
    if not isinstance(entries, dict):
        raise RepositoryScanError(
            f"coverage override {source}.entries must be a JSON object of exact tracked paths"
        )

    tracked = set(tracked_paths)
    overrides: dict[str, CoverageOverride] = {}
    for raw_path, entry in entries.items():
        try:
            normalized = normalize_artifact_path(raw_path)
        except ValueError as exc:
            raise RepositoryScanError(
                f"coverage override {source}: unsafe path {raw_path!r}: {exc}"
            ) from exc
        if normalized not in tracked:
            raise RepositoryScanError(
                f"coverage override {source}: {normalized!r} is not a tracked file in this snapshot"
            )
        if normalized in overrides:
            raise RepositoryScanError(
                f"coverage override {source}: duplicate override for {normalized!r}"
            )
        if not isinstance(entry, dict):
            raise RepositoryScanError(
                f"coverage override {source}: entry for {normalized!r} must be a JSON object"
            )
        unexpected_entry = sorted(set(entry) - _OVERRIDE_ENTRY_KEYS)
        if unexpected_entry:
            raise RepositoryScanError(
                f"coverage override {source}: entry for {normalized!r} has unexpected key(s): "
                + ", ".join(unexpected_entry)
            )
        missing = sorted(_OVERRIDE_ENTRY_KEYS - set(entry))
        if missing:
            raise RepositoryScanError(
                f"coverage override {source}: entry for {normalized!r} is missing: "
                + ", ".join(missing)
            )
        try:
            overrides[normalized] = normalize_override(
                entry["surface"], entry["classification"], entry["reason"],
            )
        except ValueError as exc:
            raise RepositoryScanError(
                f"coverage override {source}: invalid override for {normalized!r}: {exc}"
            ) from exc
    return overrides


def _project_index_document(
    context: GitContext,
    snapshot_kind: str,
    generated_at: str,
    files: tuple[IndexedFile, ...],
) -> dict[str, object]:
    return {
        "artifact_kind": "project-index",
        "schema_version": SCANNER_SCHEMA_VERSION,
        "repository_revision": context.revision,
        "generated_at": generated_at,
        "project": {
            "name": context.analysis_root.name,
            "root": ".",
            "vcs": "git",
            "dirty": context.dirty,
            "snapshot_kind": snapshot_kind,
        },
        "file_count": len(files),
        "files": [
            {
                "path": item.path,
                "bytes": item.byte_count,
                "sha256": item.sha256,
                "tracked": True,
                "content_kind": item.content_kind,
                "media_type": item.media_type,
                "extension": item.extension,
                "vcs_object_id": item.vcs_object_id,
            }
            for item in files
        ],
    }


def _coverage_document(
    context: GitContext,
    generated_at: str,
    files: tuple[IndexedFile, ...],
    overrides: dict[str, CoverageOverride],
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    unknown_count = 0
    for item in files:
        decision = classify_file(
            FileFacts(
                path=item.path,
                content_kind=item.content_kind,
                media_type=item.media_type,
                extension=item.extension,
            ),
            overrides.get(item.path),
        )
        if decision.classification == "UNKNOWN":
            unknown_count += 1
        entries.append({
            "path": item.path,
            "surface": decision.surface,
            "classification": decision.classification,
            "teaching_status": decision.teaching_status,
            "reason": decision.reason,
            "secondary_surfaces": list(decision.secondary_surfaces),
            "rule_id": decision.rule_id,
        })
    return {
        "artifact_kind": "coverage",
        "schema_version": SCANNER_SCHEMA_VERSION,
        "classification_policy_version": CLASSIFICATION_POLICY_VERSION,
        "repository_revision": context.revision,
        "generated_at": generated_at,
        "tracked_file_count": len(files),
        "unknown_count": unknown_count,
        "entries": entries,
    }


def scan_repository(options: ScanOptions) -> ScanArtifacts:
    """Build and validate both v1.1 scanner artifacts in memory without writing anything."""
    if options.snapshot_kind not in SNAPSHOT_KINDS:
        raise RepositoryScanError(
            f"unknown snapshot kind {options.snapshot_kind!r}: expected one of {', '.join(SNAPSHOT_KINDS)}"
        )
    if not isinstance(options.generated_at, str) or not _GENERATED_AT_RE.fullmatch(options.generated_at):
        raise RepositoryScanError(
            "--generated-at must match YYYY-MM-DDTHH:MM:SSZ, got "
            f"{options.generated_at!r}"
        )

    context = discover_git_context(options.root)
    files = enumerate_indexed_files(context, options.snapshot_kind)
    overrides = (
        load_coverage_overrides(options.overrides_path, {item.path for item in files})
        if options.overrides_path is not None
        else {}
    )

    project_index = _project_index_document(context, options.snapshot_kind, options.generated_at, files)
    coverage = _coverage_document(context, options.generated_at, files, overrides)
    validate_artifact(project_index)
    validate_artifact(coverage)
    return ScanArtifacts(project_index=project_index, coverage=coverage)


__all__ = [
    "GitContext",
    "IndexedFile",
    "OVERRIDE_SCHEMA_VERSION",
    "RepositoryScanError",
    "SCANNER_SCHEMA_VERSION",
    "SNAPSHOT_KINDS",
    "ScanArtifacts",
    "ScanOptions",
    "analysis_prefix_between",
    "discover_git_context",
    "enumerate_indexed_files",
    "git_capture",
    "hash_git_object",
    "load_coverage_overrides",
    "scan_repository",
    "split_nul",
]
