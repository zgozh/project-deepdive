#!/usr/bin/env python3
"""Build redacted E2 evidence for explicitly selected repository Markdown spans."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact, validate_artifact
from coverage_audit import audit_coverage
from detect_stack import _snapshot_reader
from file_classification import normalize_artifact_path
from python_static_analysis import expected_source_metadata
from repository_scan import RepositoryScanError, discover_git_context
from scan_repository import _publish, configure_stdio, utc_now


SCHEMA_VERSION = "1.3.0"
SUMMARY = "The selected repository documentation span records a repository declaration."
_GENERATED_AT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")


class RepositoryDocumentationEvidenceError(ValueError):
    """A selected documentation span could not be tied to the Phase 2 snapshot."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _validated_phase2(
    project_index: Mapping[str, Any], coverage: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    try:
        validate_artifact(project_index)
        validate_artifact(coverage)
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        raise RepositoryDocumentationEvidenceError("Phase 2 inputs are invalid") from exc
    if project_index.get("artifact_kind") != "project-index" or coverage.get("artifact_kind") != "coverage":
        raise RepositoryDocumentationEvidenceError("Phase 2 inputs have the wrong artifact kind")
    if (
        project_index.get("schema_version") != coverage.get("schema_version")
        or project_index.get("repository_revision") != coverage.get("repository_revision")
    ):
        raise RepositoryDocumentationEvidenceError("Phase 2 inputs do not identify the same snapshot")
    return project_index, coverage


def _line_offsets(payload: bytes) -> list[int]:
    """Return starts of CR, LF, or CRLF lines without adding a trailing blank line."""
    if not payload:
        return []
    starts = [0]
    for index, byte in enumerate(payload):
        is_lone_cr = byte == 0x0D and (index + 1 == len(payload) or payload[index + 1] != 0x0A)
        if (byte == 0x0A or is_lone_cr) and index + 1 < len(payload):
            starts.append(index + 1)
    return starts


def _span(payload: bytes, offsets: Sequence[int], start: int, end: int) -> bytes:
    if (
        isinstance(start, bool) or not isinstance(start, int) or start < 1
        or isinstance(end, bool) or not isinstance(end, int) or end < start
        or end > len(offsets)
    ):
        raise RepositoryDocumentationEvidenceError("a selected Markdown line range is invalid")
    first = offsets[start - 1]
    last = offsets[end] if end < len(offsets) else len(payload)
    return payload[first:last]


def _normalize_selection(selection: Mapping[str, Any] | Sequence[Any]) -> tuple[str, int, int]:
    if isinstance(selection, Mapping):
        path, start, end = selection.get("path"), selection.get("line_start"), selection.get("line_end")
    else:
        try:
            path, start, end = selection
        except (TypeError, ValueError) as exc:
            raise RepositoryDocumentationEvidenceError("a documentation selection is malformed") from exc
    try:
        normalized = normalize_artifact_path(path)
    except ValueError as exc:
        raise RepositoryDocumentationEvidenceError("a documentation path is unsafe") from exc
    if normalized != path or not normalized.lower().endswith(".md"):
        raise RepositoryDocumentationEvidenceError("a selection must name a canonical relative Markdown path")
    if (
        isinstance(start, bool) or not isinstance(start, int) or start < 1
        or isinstance(end, bool) or not isinstance(end, int) or end < start
    ):
        raise RepositoryDocumentationEvidenceError("a selected Markdown line range is invalid")
    return normalized, start, end


def _read_snapshot_sources(
    root: str | Path,
    project_index: Mapping[str, Any],
    paths: set[str],
) -> dict[str, bytes]:
    analysis_root = Path(root).resolve(strict=True)
    context = discover_git_context(analysis_root)
    snapshot_kind = project_index["project"]["snapshot_kind"]
    try:
        reader, _regular_paths = _snapshot_reader(
            analysis_root, context, snapshot_kind, project_index,
        )
    except RepositoryScanError as exc:
        raise RepositoryDocumentationEvidenceError("the Phase 2 snapshot could not be opened") from exc
    indexed = {row["path"]: row for row in project_index["files"]}
    payloads: dict[str, bytes] = {}
    for path in sorted(paths):
        entry = indexed.get(path)
        if entry is None or entry.get("tracked") is not True:
            raise RepositoryDocumentationEvidenceError("a selected Markdown file is not in the Phase 2 index")
        if entry.get("content_kind") in {"binary", "symlink", "gitlink"}:
            raise RepositoryDocumentationEvidenceError("a selected Markdown file is not regular text")
        try:
            payload = reader(path, entry)
        except Exception as exc:
            raise RepositoryDocumentationEvidenceError("a selected Markdown file does not match the Phase 2 snapshot") from exc
        if b"\x00" in payload:
            raise RepositoryDocumentationEvidenceError("a selected Markdown file is not UTF-8 text")
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise RepositoryDocumentationEvidenceError("a selected Markdown file is not UTF-8 text") from exc
        payloads[path] = payload
    return payloads


def _build_records(
    root: str | Path,
    project_index: Mapping[str, Any],
    selections: Sequence[Mapping[str, Any] | Sequence[Any]],
) -> list[dict[str, Any]]:
    normalized = [_normalize_selection(item) for item in selections]
    if not normalized or len(set(normalized)) != len(normalized):
        raise RepositoryDocumentationEvidenceError("at least one unique documentation selection is required")
    payloads = _read_snapshot_sources(root, project_index, {path for path, _start, _end in normalized})
    records: list[dict[str, Any]] = []
    for path, start, end in normalized:
        payload = payloads[path]
        offsets = _line_offsets(payload)
        selected = _span(payload, offsets, start, end)
        file_digest = hashlib.sha256(payload).hexdigest()
        span_digest = hashlib.sha256(selected).hexdigest()
        identity = [
            "repository_documentation",
            project_index["repository_revision"],
            project_index["project"]["snapshot_kind"],
            path,
            start,
            end,
            file_digest,
            span_digest,
        ]
        evidence_id = "EVID-" + hashlib.sha256(_canonical_bytes(identity)).hexdigest()[:24]
        records.append({
            "id": evidence_id,
            "level": "E2",
            "kind": "repository_documentation",
            "summary": SUMMARY,
            "confidence": 1.0,
            "locator": {"path": path, "line_start": start, "line_end": end},
            "source_bytes": {
                "file_sha256": file_digest,
                "span_sha256": span_digest,
                "span_bytes": len(selected),
            },
        })
    records.sort(key=lambda item: (
        item["locator"]["path"], item["locator"]["line_start"], item["locator"]["line_end"], item["id"],
    ))
    if len({item["id"] for item in records}) != len(records):
        raise RepositoryDocumentationEvidenceError("documentation evidence ID collision")
    return records


def build_repository_documentation_evidence(
    *,
    root: str | Path,
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    selections: Sequence[Mapping[str, Any] | Sequence[Any]],
    generated_at: str | None = None,
    g01_status: str | None = None,
    unknown_files: int | None = None,
) -> dict[str, Any]:
    """Build E2 repository-declaration evidence from explicit inclusive line ranges."""
    index, coverage_artifact = _validated_phase2(project_index, coverage)
    if (g01_status is None) != (unknown_files is None):
        raise RepositoryDocumentationEvidenceError("G01 snapshot metadata is incomplete")
    if g01_status is None:
        audit = audit_coverage(index, coverage_artifact, Path(root))
        if audit.status == "FAIL":
            raise RepositoryDocumentationEvidenceError("G01 failed for the supplied Phase 2 snapshot")
        g01_status = audit.status
        unknown_files = audit.measurements["unknown_files"]
    try:
        metadata = expected_source_metadata(index, coverage_artifact, g01_status, unknown_files)
    except Exception as exc:
        raise RepositoryDocumentationEvidenceError("G01 metadata does not match Phase 2") from exc
    timestamp = generated_at or utc_now()
    if not isinstance(timestamp, str) or not _GENERATED_AT_RE.fullmatch(timestamp):
        raise RepositoryDocumentationEvidenceError("generation timestamp is invalid")
    evidence = {
        "artifact_kind": "evidence",
        "schema_version": SCHEMA_VERSION,
        "repository_revision": index["repository_revision"],
        "generated_at": timestamp,
        "snapshot_kind": index["project"]["snapshot_kind"],
        "source_metadata": metadata,
        "items": _build_records(root, index, selections),
    }
    try:
        validate_artifact(evidence)
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        raise RepositoryDocumentationEvidenceError("generated repository documentation evidence is invalid") from exc
    return evidence


def validate_repository_documentation_evidence(
    evidence: Mapping[str, Any],
    *,
    root: str | Path,
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    g01_status: str,
    unknown_files: int,
) -> None:
    """Rebuild every selected record from pinned bytes and require exact equality."""
    try:
        validate_artifact(evidence)
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        raise RepositoryDocumentationEvidenceError("repository documentation evidence is invalid") from exc
    if evidence.get("schema_version") != SCHEMA_VERSION:
        raise RepositoryDocumentationEvidenceError("unsupported repository documentation evidence version")
    if (
        evidence.get("repository_revision") != project_index.get("repository_revision")
        or evidence.get("snapshot_kind") != project_index.get("project", {}).get("snapshot_kind")
    ):
        raise RepositoryDocumentationEvidenceError("repository documentation evidence identifies another snapshot")
    selectors = [
        (item["locator"]["path"], item["locator"]["line_start"], item["locator"]["line_end"])
        for item in evidence["items"]
    ]
    expected = build_repository_documentation_evidence(
        root=root,
        project_index=project_index,
        coverage=coverage,
        selections=selectors,
        generated_at=evidence["generated_at"],
        g01_status=g01_status,
        unknown_files=unknown_files,
    )
    if dict(evidence) != expected:
        raise RepositoryDocumentationEvidenceError("repository documentation evidence does not match pinned source bytes")


def _parse_selection(value: str) -> tuple[str, int, int]:
    path, separator, line_end = value.rpartition(":")
    path, separator_start, line_start = path.rpartition(":")
    if not separator or not separator_start:
        raise argparse.ArgumentTypeError("selection must be PATH:LINE_START:LINE_END")
    try:
        return path, int(line_start), int(line_end)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("selection line numbers must be integers") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build redacted evidence for explicit repository Markdown declarations",
    )
    parser.add_argument("--root", required=True, type=Path, help="analysed Git repository or subtree")
    parser.add_argument("--project-index", required=True, type=Path, help="Phase 2 project-index.json")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage.json")
    parser.add_argument("--selection", action="append", required=True, type=_parse_selection,
                        metavar="PATH:LINE_START:LINE_END",
                        help="relative Markdown path and inclusive one-based line range; repeat as needed")
    parser.add_argument("--out", required=True, type=Path,
                        help="new output directory outside the target; receives evidence.json")
    parser.add_argument("--generated-at", default=None, metavar="YYYY-MM-DDTHH:MM:SSZ",
                        help="fixed output timestamp; defaults to current UTC")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.generated_at is not None and not _GENERATED_AT_RE.fullmatch(args.generated_at):
        parser.error("--generated-at must match YYYY-MM-DDTHH:MM:SSZ")
    try:
        project_index = load_artifact(args.project_index)
        coverage = load_artifact(args.coverage)
        evidence = build_repository_documentation_evidence(
            root=args.root,
            project_index=project_index,
            coverage=coverage,
            selections=args.selection,
            generated_at=args.generated_at,
        )
        from phase3_run import _output_path

        output = _output_path(args.root, args.out)
        _publish(output, {"evidence.json": dumps_artifact(evidence)})
    except Exception:
        print("status=FAIL\nlimitations=DOCUMENTATION_EVIDENCE_INVALID")
        return 1
    print(f"published repository documentation evidence; records={len(evidence['items'])} snapshot={evidence['snapshot_kind']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
