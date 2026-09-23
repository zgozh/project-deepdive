#!/usr/bin/env python3
"""Reusable semantic coverage audit for Project DeepDive Phase 2 artifacts.

Schema validation is necessary but insufficient: the two scanner artifacts can
both be individually valid and still disagree with each other, with the Git
tracked path set, or with the snapshot they claim to describe.  This module is
the deterministic G01 check for that, and later phases can call it unchanged.

Every mismatch is a failure, never a warning.  Without ``require_complete`` an
artifact that honestly reports unresolved files is ``PARTIAL`` and still a
successful scan product; with the flag it fails.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from file_classification import RULE_IDS, SURFACES, normalize_artifact_path
from repository_scan import (
    RepositoryScanError,
    SAMPLE_BYTES,
    analysis_prefix_between,
    git_capture,
    hash_git_object,
)

VIOLATION_CODES: tuple[str, ...] = (
    "SCHEMA_INVALID",
    "REVISION_MISMATCH",
    "TIMESTAMP_MISMATCH",
    "COUNT_MISMATCH",
    "DUPLICATE_PATH",
    "PATH_SET_MISMATCH",
    "UNSAFE_PATH",
    "V11_FIELD_MISSING",
    "SURFACE_INVALID",
    "REASON_MISSING",
    "UNKNOWN_COUNT_MISMATCH",
    "UNKNOWN_REMAINS",
    "SNAPSHOT_FILE_MISSING",
    "SNAPSHOT_SIZE_MISMATCH",
    "SNAPSHOT_HASH_MISMATCH",
    "GIT_OBJECT_MISSING",
)

_STATUS_PASS = "PASS"
_STATUS_PARTIAL = "PARTIAL"
_STATUS_FAIL = "FAIL"

_INDEX_V11_FIELDS = ("content_kind", "media_type", "extension", "vcs_object_id")
_COVERAGE_V11_FIELDS = ("secondary_surfaces", "rule_id")
_REASON_REQUIRED_CLASSIFICATIONS = ("GENERATED", "VENDOR", "IGNORED_WITH_REASON")
_GITLINK_CONTENT_KIND = "gitlink"


@dataclass(frozen=True)
class AuditViolation:
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class CoverageAuditResult:
    status: str
    measurements: dict[str, int]
    violations: tuple[AuditViolation, ...]


def _stream_hash(target: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    with open(target, "rb") as handle:
        while True:
            chunk = handle.read(SAMPLE_BYTES * 8)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
    return total, digest.hexdigest()


def _gitlink_payload(object_id: str) -> tuple[int, str]:
    payload = object_id.encode("ascii")
    return len(payload), hashlib.sha256(payload).hexdigest()


def _measurements(project_index: Mapping[str, object], coverage: Mapping[str, object],
                  violations: int, unknowns: int) -> dict[str, int]:
    files = project_index.get("files")
    entries = coverage.get("entries")
    return {
        "indexed_files": len(files) if isinstance(files, list) else 0,
        "coverage_entries": len(entries) if isinstance(entries, list) else 0,
        "unknown_files": unknowns,
        "violations": violations,
    }


def _repository_layout(root: Path) -> tuple[Path, str]:
    top = git_capture(Path(root), "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise RepositoryScanError(
            f"{root}: not inside a Git repository, so a git-tree snapshot cannot be verified"
        )
    repository_root = Path(os.fsdecode(top.stdout).strip())
    return repository_root, analysis_prefix_between(repository_root, root)


def audit_coverage(
    project_index: Mapping[str, object],
    coverage: Mapping[str, object],
    root: Path,
    require_complete: bool = False,
) -> CoverageAuditResult:
    """Audit two coverage artifacts against each other and against the snapshot."""
    violations: list[AuditViolation] = []

    def add(code: str, path: str, message: str) -> None:
        violations.append(AuditViolation(code=code, path=path, message=message))

    schema_failures: list[tuple[str, str]] = []
    for label, artifact in (("project-index", project_index), ("coverage", coverage)):
        try:
            validate_artifact(artifact)
        except (ArtifactValidationError, KeyError, TypeError, AttributeError) as exc:
            schema_failures.append((label, str(exc)))
    if schema_failures:
        for label, message in schema_failures:
            add("SCHEMA_INVALID", label, f"{label} does not satisfy its schema: {message}")
        ordered = tuple(sorted(violations, key=lambda item: (item.code, item.path, item.message)))
        return CoverageAuditResult(
            status=_STATUS_FAIL,
            measurements=_measurements(project_index, coverage, len(ordered), 0),
            violations=ordered,
        )

    files = project_index["files"]
    entries = coverage["entries"]
    index_v11 = project_index.get("schema_version") == "1.1.0"
    coverage_v11 = coverage.get("schema_version") == "1.1.0"

    if project_index["file_count"] != len(files):
        add("COUNT_MISMATCH", "project-index",
            f"file_count {project_index['file_count']} does not match {len(files)} file entries")
    if coverage["tracked_file_count"] != len(entries):
        add("COUNT_MISMATCH", "coverage",
            f"tracked_file_count {coverage['tracked_file_count']} does not match {len(entries)} entries")

    unknown_entries = [entry for entry in entries if entry.get("classification") == "UNKNOWN"]
    if coverage["unknown_count"] != len(unknown_entries):
        add("UNKNOWN_COUNT_MISMATCH", "coverage",
            f"unknown_count {coverage['unknown_count']} does not match "
            f"{len(unknown_entries)} UNKNOWN entries")

    if project_index["repository_revision"] != coverage["repository_revision"]:
        add("REVISION_MISMATCH", "coverage",
            "coverage.repository_revision does not match project-index.repository_revision")
    if project_index["generated_at"] != coverage["generated_at"]:
        add("TIMESTAMP_MISMATCH", "coverage",
            "coverage.generated_at does not match project-index.generated_at")

    index_paths = [item["path"] for item in files]
    coverage_paths = [entry["path"] for entry in entries]
    for label, paths in (("project-index", index_paths), ("coverage", coverage_paths)):
        seen: set[str] = set()
        for path in paths:
            if path in seen:
                add("DUPLICATE_PATH", path, f"{label} lists {path!r} more than once")
            seen.add(path)

    unsafe: set[str] = set()
    for path in set(index_paths) | set(coverage_paths):
        try:
            normalize_artifact_path(path)
        except ValueError as exc:
            unsafe.add(path)
            add("UNSAFE_PATH", path, f"artifact path is not a safe relative POSIX path: {exc}")

    if set(index_paths) != set(coverage_paths):
        missing_from_coverage = sorted(set(index_paths) - set(coverage_paths))
        missing_from_index = sorted(set(coverage_paths) - set(index_paths))
        details = []
        if missing_from_coverage:
            details.append(f"missing from coverage: {', '.join(missing_from_coverage[:5])}")
        if missing_from_index:
            details.append(f"missing from project-index: {', '.join(missing_from_index[:5])}")
        add("PATH_SET_MISMATCH", "coverage", "; ".join(details))

    for entry in entries:
        path = entry["path"]
        if coverage_v11:
            missing = [name for name in _COVERAGE_V11_FIELDS if name not in entry]
            if missing:
                add("V11_FIELD_MISSING", path,
                    f"scanner coverage entry is missing: {', '.join(missing)}")
            rule_id = entry.get("rule_id")
            if rule_id is not None and rule_id not in RULE_IDS:
                add("V11_FIELD_MISSING", path, f"unknown rule_id {rule_id!r}")
            secondary = entry.get("secondary_surfaces")
            if isinstance(secondary, list):
                if entry.get("surface") in secondary:
                    add("SURFACE_INVALID", path,
                        "secondary_surfaces must not repeat the primary surface")
                if len(set(secondary)) != len(secondary):
                    add("SURFACE_INVALID", path, "secondary_surfaces contains duplicates")
                if secondary != sorted(secondary):
                    add("SURFACE_INVALID", path, "secondary_surfaces is not sorted")
                unknown_surfaces = sorted({name for name in secondary if name not in SURFACES})
                if unknown_surfaces:
                    add("SURFACE_INVALID", path,
                        f"unknown secondary surface(s): {', '.join(unknown_surfaces)}")
        if entry.get("classification") in _REASON_REQUIRED_CLASSIFICATIONS and not entry.get("reason"):
            add("REASON_MISSING", path,
                f"{entry.get('classification')} entry has no reason explaining the classification")

    if index_v11:
        for item in files:
            missing = [name for name in _INDEX_V11_FIELDS if name not in item]
            if missing:
                add("V11_FIELD_MISSING", item["path"],
                    f"scanner file entry is missing: {', '.join(missing)}")

    if require_complete and unknown_entries:
        for entry in unknown_entries:
            add("UNKNOWN_REMAINS", entry["path"],
                "completeness was required but this tracked file is still UNKNOWN")

    _verify_snapshot(project_index, files, root, unsafe, add)

    ordered = tuple(sorted(violations, key=lambda item: (item.code, item.path, item.message)))
    if ordered:
        status = _STATUS_FAIL
    elif unknown_entries:
        status = _STATUS_PARTIAL
    else:
        status = _STATUS_PASS
    return CoverageAuditResult(
        status=status,
        measurements=_measurements(project_index, coverage, len(ordered), len(unknown_entries)),
        violations=ordered,
    )


def _verify_snapshot(
    project_index: Mapping[str, object],
    files: list,
    root: Path,
    unsafe: set[str],
    add,
) -> None:
    snapshot_kind = project_index["project"]["snapshot_kind"]
    if snapshot_kind == "worktree":
        _verify_worktree(files, root, unsafe, add)
        return
    if snapshot_kind == "git-tree":
        _verify_git_tree(project_index, files, root, unsafe, add)
        return
    add("SNAPSHOT_FILE_MISSING", "project-index",
        f"unsupported snapshot_kind {snapshot_kind!r}: cannot verify the snapshot")


def _verify_worktree(files: list, root: Path, unsafe: set[str], add) -> None:
    for item in files:
        path = item["path"]
        if path in unsafe:
            continue
        target = Path(root) / path
        content_kind = item.get("content_kind")
        if content_kind == _GITLINK_CONTENT_KIND:
            byte_count, sha256 = _gitlink_payload(item["vcs_object_id"])
        elif content_kind == "symlink":
            try:
                payload = os.fsencode(os.readlink(target))
            except OSError:
                add("SNAPSHOT_FILE_MISSING", path, f"tracked symlink is missing from the worktree: {path}")
                continue
            byte_count, sha256 = len(payload), hashlib.sha256(payload).hexdigest()
        else:
            if not target.is_file():
                add("SNAPSHOT_FILE_MISSING", path, f"tracked file is missing from the worktree: {path}")
                continue
            try:
                byte_count, sha256 = _stream_hash(target)
            except OSError as exc:
                add("SNAPSHOT_FILE_MISSING", path, f"tracked file cannot be read: {path}: {exc}")
                continue
        _compare_payload(item, path, byte_count, sha256, add)


def _verify_git_tree(
    project_index: Mapping[str, object],
    files: list,
    root: Path,
    unsafe: set[str],
    add,
) -> None:
    revision = project_index["repository_revision"]
    repository_root, prefix = _repository_layout(Path(root))
    for item in files:
        path = item["path"]
        if path in unsafe:
            continue
        repository_path = f"{prefix}/{path}" if prefix else path
        resolved = git_capture(repository_root, "rev-parse", f"{revision}:{repository_path}")
        if resolved.returncode != 0:
            add("GIT_OBJECT_MISSING", path,
                f"{path} does not resolve at revision {revision}")
            continue
        object_id = resolved.stdout.decode("ascii", "replace").strip()
        recorded_object_id = item.get("vcs_object_id")
        if recorded_object_id is not None and object_id != recorded_object_id:
            add("GIT_OBJECT_MISSING", path,
                f"{path} resolves to {object_id} at revision {revision} but the artifact records "
                f"{recorded_object_id}")
            continue
        if item.get("content_kind") == _GITLINK_CONTENT_KIND:
            byte_count, sha256 = _gitlink_payload(item["vcs_object_id"])
        else:
            try:
                byte_count, sha256, _ = hash_git_object(repository_root, object_id, 0)
            except RepositoryScanError as exc:
                add("GIT_OBJECT_MISSING", path, f"cannot read {path} at revision {revision}: {exc}")
                continue
        _compare_payload(item, path, byte_count, sha256, add)


def _compare_payload(item: Mapping[str, object], path: str, byte_count: int, sha256: str, add) -> None:
    if item["bytes"] != byte_count:
        add("SNAPSHOT_SIZE_MISMATCH", path,
            f"recorded {item['bytes']} bytes but the snapshot payload has {byte_count}")
    elif item["sha256"] != sha256:
        add("SNAPSHOT_HASH_MISMATCH", path,
            "recorded sha256 does not match the snapshot payload")


__all__ = [
    "AuditViolation",
    "CoverageAuditResult",
    "VIOLATION_CODES",
    "audit_coverage",
]
