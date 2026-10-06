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
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from subprocess import DEVNULL, PIPE, Popen, TimeoutExpired

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


class UnsafeWorktreePathError(RepositoryScanError):
    """A tracked worktree path resolves through symlink/reparse indirection."""


class InvalidGitRevisionError(RepositoryScanError):
    """A declared repository revision is not a full commit object ID."""


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
class GitSnapshotEntry:
    path: str
    mode: str
    object_id: str
    object_type: str


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


def read_git_blobs_batch(
    repository_root: Path,
    object_ids: list[str],
    sample_bytes: int = 0,
) -> dict[str, tuple[int, str, bytes] | None]:
    """Read requested Git blobs through one bounded-stream cat-file process."""
    if not object_ids:
        return {}

    process = None
    input_closed = False
    payloads: dict[str, tuple[int, str, bytes] | None] = {}
    try:
        process = Popen(
            ["git", "cat-file", "--batch"],
            cwd=str(repository_root),
            stdin=PIPE,
            stdout=PIPE,
            stderr=DEVNULL,
        )
        if process.stdin is None or process.stdout is None:
            raise RepositoryScanError("Git cat-file batch pipes are unavailable")

        for object_id in object_ids:
            normalized_id = object_id.lower()
            if (
                len(normalized_id) not in {40, 64}
                or any(character not in "0123456789abcdef" for character in normalized_id)
            ):
                raise RepositoryScanError("Git tree contains an invalid object id")

            process.stdin.write(normalized_id.encode("ascii") + b"\n")
            process.stdin.flush()
            header = process.stdout.readline(128)
            if not header.endswith(b"\n"):
                raise RepositoryScanError("Git cat-file batch returned an incomplete header")
            fields = header[:-1].split(b" ")
            if len(fields) == 2 and fields[1] == b"missing":
                try:
                    returned_id = fields[0].decode("ascii").lower()
                except UnicodeDecodeError:
                    raise RepositoryScanError("Git cat-file batch returned a malformed header") from None
                if returned_id != normalized_id:
                    raise RepositoryScanError("Git cat-file batch returned an unexpected object id")
                payloads[object_id] = None
                continue
            if len(fields) != 3:
                raise RepositoryScanError("Git cat-file batch returned a malformed header")
            try:
                returned_id = fields[0].decode("ascii").lower()
                object_type = fields[1].decode("ascii")
                size_text = fields[2].decode("ascii")
            except UnicodeDecodeError:
                raise RepositoryScanError("Git cat-file batch returned a malformed header") from None
            if (
                returned_id != normalized_id
                or object_type != "blob"
                or not size_text.isascii()
                or not size_text.isdecimal()
            ):
                raise RepositoryScanError("Git cat-file batch returned an invalid blob header")
            remaining = int(size_text)
            digest = hashlib.sha256()
            byte_count = 0
            sample = bytearray()
            while remaining:
                chunk = process.stdout.read(min(_CHUNK_BYTES, remaining))
                if not chunk:
                    raise RepositoryScanError("Git cat-file batch returned a truncated blob")
                digest.update(chunk)
                byte_count += len(chunk)
                remaining -= len(chunk)
                if len(sample) < sample_bytes:
                    sample += chunk[: sample_bytes - len(sample)]
            if process.stdout.read(1) != b"\n":
                raise RepositoryScanError("Git cat-file batch returned an invalid blob delimiter")
            payloads[object_id] = (byte_count, digest.hexdigest(), bytes(sample))

        process.stdin.close()
        input_closed = True
        if process.stdout.read(1) != b"":
            raise RepositoryScanError("Git cat-file batch returned unexpected trailing data")
        if process.wait() != 0:
            raise RepositoryScanError("Git cat-file batch failed to read declared objects")
        return payloads
    except RepositoryScanError:
        raise
    except (OSError, UnicodeError, ValueError, OverflowError):
        raise RepositoryScanError("Git cat-file batch could not read declared objects") from None
    finally:
        if process is not None:
            if process.stdin is not None and not input_closed:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except TimeoutExpired:
                    process.kill()
                    process.wait()
            if process.stdout is not None:
                process.stdout.close()


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


def enumerate_git_snapshot_entries(
    context: GitContext,
    snapshot_kind: str,
    revision: str | None = None,
) -> tuple[GitSnapshotEntry, ...]:
    """Read authoritative path and object metadata without touching worktree files."""
    if snapshot_kind == "worktree":
        result = _run_git(context.repository_root, "ls-files", "--stage", "-z")
        failure = "cannot enumerate the current Git index"
    elif snapshot_kind == "git-tree":
        selected_revision = context.revision if revision is None else revision
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", selected_revision):
            raise InvalidGitRevisionError(
                f"cannot enumerate Git tree at invalid revision {selected_revision!r}"
            )
        object_type = _run_git(context.repository_root, "cat-file", "-t", selected_revision)
        if object_type.returncode == 0:
            actual_type = object_type.stdout.decode("ascii", "replace").strip()
            if actual_type != "commit":
                raise InvalidGitRevisionError(
                    f"repository revision {selected_revision} names a Git {actual_type} object, "
                    "expected a commit"
                )
        result = _run_git(
            context.repository_root, "ls-tree", "-r", "-z", selected_revision,
        )
        failure = f"cannot enumerate Git tree at revision {selected_revision}"
    else:
        raise RepositoryScanError(
            f"unknown snapshot kind {snapshot_kind!r}: expected one of {', '.join(SNAPSHOT_KINDS)}"
        )
    if result.returncode != 0:
        raise RepositoryScanError(
            f"{failure}: {result.stderr.decode('utf-8', 'replace').strip()}"
        )

    entries: list[GitSnapshotEntry] = []
    for record in split_nul(result.stdout):
        metadata, separator, raw_path = record.partition(b"\t")
        fields = metadata.decode("ascii").split(" ")
        if snapshot_kind == "worktree":
            if not separator or len(fields) != 3:
                raise RepositoryScanError(
                    f"unexpected git ls-files --stage record: {metadata!r}"
                )
            mode, object_id, stage = fields
            if stage != "0":
                path = _decode_path(raw_path, "git ls-files") if separator else "<unknown>"
                raise RepositoryScanError(
                    f"unresolved merge stage {stage} for tracked path {path!r}; "
                    "resolve the conflict index before scanning"
                )
            object_type = ""
        else:
            if not separator or len(fields) < 3:
                raise RepositoryScanError(f"unexpected git ls-tree record: {metadata!r}")
            mode, object_type, object_id = fields[0], fields[1], fields[2]

        raw = _decode_path(raw_path, "git snapshot")
        artifact_path = _artifact_path(raw, context)
        if artifact_path is not None:
            entries.append(GitSnapshotEntry(
                path=artifact_path,
                mode=mode,
                object_id=object_id,
                object_type=object_type,
            ))

    entries.sort(key=lambda item: item.path)
    for previous, current in zip(entries, entries[1:]):
        if previous.path == current.path:
            raise RepositoryScanError(f"duplicate tracked path in Git snapshot: {current.path}")
    return tuple(entries)


def _is_reparse_point(path: Path, info) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if getattr(info, "st_file_attributes", 0) & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())


def _ensure_within_root(root: Path, path: Path, artifact_path: str) -> None:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafeWorktreePathError(
            f"unsafe tracked path {artifact_path!r}: resolved path is outside the analysis root"
        ) from exc


def guard_worktree_path(
    root: Path,
    artifact_path: str,
    expected_kind: str = "file",
) -> Path:
    """Validate a tracked path before reading it from the worktree.

    The final symlink is allowed only for a Git symlink entry, whose payload is
    obtained with ``readlink``.  Regular files and all ancestor directories must
    contain no symlink or Windows reparse-point indirection.
    """
    try:
        normalized = normalize_artifact_path(artifact_path)
    except ValueError as exc:
        raise UnsafeWorktreePathError(
            f"unsafe tracked path {artifact_path!r}: {exc}"
        ) from exc
    if expected_kind not in ("file", "symlink"):
        raise ValueError(f"unsupported expected worktree kind {expected_kind!r}")

    analysis_root = Path(root).resolve(strict=True)
    target = analysis_root.joinpath(*normalized.split("/"))
    current = analysis_root
    parts = normalized.split("/")
    for component in parts[:-1]:
        current = current / component
        info = os.lstat(current)
        if _is_reparse_point(current, info):
            raise UnsafeWorktreePathError(
                f"unsafe tracked path {normalized!r}: symlink/reparse ancestor {current}"
            )
        if not stat.S_ISDIR(info.st_mode):
            raise UnsafeWorktreePathError(
                f"unsafe tracked path {normalized!r}: ancestor {current} is not a directory"
            )
        _ensure_within_root(analysis_root, current, normalized)

    info = os.lstat(target)
    if expected_kind == "symlink":
        if not stat.S_ISLNK(info.st_mode):
            raise UnsafeWorktreePathError(
                f"unsafe tracked symlink {normalized!r}: final entry is not a symbolic link"
            )
        return target

    if _is_reparse_point(target, info):
        raise UnsafeWorktreePathError(
            f"unsafe tracked path {normalized!r}: final entry is a symlink/reparse point, "
            "expected a regular file"
        )
    if not stat.S_ISREG(info.st_mode):
        raise UnsafeWorktreePathError(
            f"unsafe tracked path {normalized!r}: final entry is not a regular file"
        )
    _ensure_within_root(analysis_root, target, normalized)
    return target


def _finalize(files: list[IndexedFile]) -> tuple[IndexedFile, ...]:
    files.sort(key=lambda item: item.path)
    for previous, current in zip(files, files[1:]):
        if previous.path == current.path:
            raise RepositoryScanError(f"duplicate tracked path in the index: {current.path}")
    return tuple(files)


def _enumerate_worktree(context: GitContext) -> tuple[IndexedFile, ...]:
    files: list[IndexedFile] = []
    for entry in enumerate_git_snapshot_entries(context, "worktree"):
        if entry.mode == _MODE_GITLINK:
            files.append(_gitlink_file(entry.path, entry.object_id))
            continue

        if entry.mode == _MODE_SYMLINK:
            try:
                target = guard_worktree_path(context.analysis_root, entry.path, "symlink")
                link_target = os.readlink(target)
            except (OSError, UnsafeWorktreePathError) as exc:
                raise RepositoryScanError(
                    f"cannot safely read tracked symlink {entry.path}: {exc}; "
                    "re-run with --snapshot git-tree to hash the committed revision"
                ) from exc
            payload = os.fsencode(link_target)
            files.append(_indexed_file(
                entry.path, "symlink", entry.object_id, len(payload),
                hashlib.sha256(payload).hexdigest(), payload[:SAMPLE_BYTES],
            ))
            continue

        try:
            target = guard_worktree_path(context.analysis_root, entry.path, "file")
        except (OSError, UnsafeWorktreePathError) as exc:
            raise RepositoryScanError(
                f"cannot safely read tracked file {entry.path}: {exc}; "
                "re-run with --snapshot git-tree to hash the committed revision"
            ) from exc
        byte_count, sha256, sample = _hash_worktree_file(target, SAMPLE_BYTES)
        files.append(_indexed_file(entry.path, "file", entry.object_id, byte_count, sha256, sample))

    return _finalize(files)


def _enumerate_git_tree(context: GitContext) -> tuple[IndexedFile, ...]:
    files: list[IndexedFile] = []
    entries = enumerate_git_snapshot_entries(context, "git-tree", context.revision)
    blob_entries: list[GitSnapshotEntry] = []
    for entry in entries:
        if entry.mode == _MODE_GITLINK or entry.object_type == "commit":
            files.append(_gitlink_file(entry.path, entry.object_id))
            continue
        if entry.object_type != "blob":
            raise RepositoryScanError(
                f"unsupported Git object type {entry.object_type!r} for tracked path {entry.path!r}"
            )
        blob_entries.append(entry)

    blob_ids = list(dict.fromkeys(entry.object_id for entry in blob_entries))
    blob_payloads = read_git_blobs_batch(context.repository_root, blob_ids, SAMPLE_BYTES)
    for entry in blob_entries:
        payload = blob_payloads.get(entry.object_id)
        if payload is None:
            raise RepositoryScanError(
                f"cannot read Git object {entry.object_id} at revision {context.revision}"
            )
        byte_count, sha256, sample = payload
        git_kind = "symlink" if entry.mode == _MODE_SYMLINK else "file"
        files.append(_indexed_file(entry.path, git_kind, entry.object_id, byte_count, sha256, sample))

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
    "read_git_blobs_batch",
    "scan_repository",
    "split_nul",
]
