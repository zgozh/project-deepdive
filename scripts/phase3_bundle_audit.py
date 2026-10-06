#!/usr/bin/env python3
"""Read-only cross-artifact audit for the accepted Phase 2 and Phase 3 bundles."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from coverage_audit import CoverageAuditResult, audit_coverage
from detect_stack import _snapshot_reader
from frontend_snapshot_analysis import validate_frontend_analysis_bundle
from java_framework_static_analysis import validate_java_framework_bundle
from java_static_analysis import validate_java_analysis_bundle
from python_static_analysis import expected_source_metadata, validate_analysis_bundle
from repository_scan import (
    RepositoryScanError,
    discover_git_context,
    enumerate_git_snapshot_entries,
)
from stack_detection import validate_evidence_references


_PASS = "PASS"
_PARTIAL = "PARTIAL"
_NOT_RUN = "NOT_RUN"
_ADAPTERS = ("python", "java", "frontend")
_FRONTEND_EXTENSIONS = {".js", ".jsx", ".ts", ".tsx"}


class _AuditInputError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _new_report() -> dict[str, Any]:
    adapters = {
        name: {
            "status": _NOT_RUN,
            "version": None,
            "applicable_files": 0,
            "analyzed_files": 0,
            "skipped_files": 0,
            "partial_files": 0,
            "limitations": [],
        }
        for name in _ADAPTERS
    }
    return {
        "status": "FAIL",
        "g01": {"before": _NOT_RUN, "after": _NOT_RUN},
        "adapters": adapters,
        "counts": {
            "tracked_files": 0,
            "covered_tracked_files": 0,
            "unknown_tracked_files": 0,
            "analyzed_files": 0,
            "skipped_files": 0,
            "partial_files": 0,
            "unreported_source_files": 0,
            "outside_analyzer_files": 0,
        },
        "limitations": [],
    }


def _add_code(report: dict[str, Any], code: str, adapter: str | None = None) -> None:
    codes = set(report["limitations"])
    codes.add(code)
    report["limitations"] = sorted(codes)
    if adapter in report["adapters"]:
        adapter_codes = set(report["adapters"][adapter]["limitations"])
        adapter_codes.add(code)
        report["adapters"][adapter]["limitations"] = sorted(adapter_codes)


def _validate_input(
    artifact: Any,
    expected_kind: str,
    versions: set[str],
) -> Mapping[str, Any]:
    if not isinstance(artifact, Mapping) or artifact.get("artifact_kind") != expected_kind:
        raise _AuditInputError("INPUT_INVALID")
    version = artifact.get("schema_version")
    if version not in versions:
        raise _AuditInputError("UNSUPPORTED_VERSION")
    try:
        validate_artifact(artifact)
    except (ArtifactValidationError, KeyError, TypeError, ValueError):
        raise _AuditInputError("INPUT_INVALID") from None
    return artifact


def _phase2_and_phase3a_inputs(
    project_index: Any,
    coverage: Any,
    stack_profile: Any,
    phase3a_evidence: Any,
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]]:
    index = _validate_input(project_index, "project-index", {"1.0.0", "1.1.0"})
    coverage_artifact = _validate_input(coverage, "coverage", {"1.0.0", "1.1.0"})
    if index["schema_version"] != coverage_artifact["schema_version"]:
        raise _AuditInputError("VERSION_MISMATCH")
    profile = _validate_input(stack_profile, "stack-profile", {"1.1.0"})
    evidence = _validate_input(phase3a_evidence, "evidence", {"1.1.0"})
    return index, coverage_artifact, profile, evidence


def _optional_pair(
    report: dict[str, Any],
    adapter: str,
    analysis: Any,
    evidence: Any,
    analysis_versions: set[str],
    evidence_version: str,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    if (analysis is None) != (evidence is None):
        _add_code(report, "PAIR_INCOMPLETE", adapter)
        return None, None
    if analysis is None:
        return None, None
    try:
        analysis_artifact = _validate_input(analysis, "static-analysis", analysis_versions)
        evidence_artifact = _validate_input(evidence, "evidence", {evidence_version})
    except _AuditInputError as exc:
        _add_code(report, exc.code, adapter)
        return None, None
    return analysis_artifact, evidence_artifact


def _family_paths(
    project_index: Any,
    coverage: Any,
) -> tuple[dict[str, set[str]], int, int, int]:
    files = project_index.get("files") if isinstance(project_index, Mapping) else None
    entries = coverage.get("entries") if isinstance(coverage, Mapping) else None
    file_rows = files if isinstance(files, list) else []
    coverage_rows = entries if isinstance(entries, list) else []
    coverage_by_path = {
        row.get("path"): row
        for row in coverage_rows
        if isinstance(row, Mapping) and isinstance(row.get("path"), str)
    }
    python_paths: set[str] = set()
    java_paths: set[str] = set()
    frontend_paths: set[str] = set()
    unique_paths: set[str] = set()
    for row in file_rows:
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            continue
        path = row["path"]
        unique_paths.add(path)
        suffix = PurePosixPath(path).suffix.lower()
        if suffix == ".py":
            python_paths.add(path)
        elif suffix == ".java":
            java_paths.add(path)
        elif suffix in _FRONTEND_EXTENSIONS:
            coverage_entry = coverage_by_path.get(path, {})
            secondary = coverage_entry.get("secondary_surfaces", [])
            if (
                coverage_entry.get("surface") == "frontend"
                or isinstance(secondary, list) and "frontend" in secondary
            ):
                frontend_paths.add(path)
    family_paths = {
        "python": python_paths,
        "java": java_paths,
        "frontend": frontend_paths,
    }
    known_family = set().union(*family_paths.values())
    unknown = sum(
        1 for row in coverage_rows
        if isinstance(row, Mapping) and row.get("classification") == "UNKNOWN"
    )
    tracked = len(file_rows)
    covered = max(0, tracked - unknown)
    outside = max(0, len(unique_paths) - len(known_family))
    return family_paths, tracked, covered, outside


def _status_signature(result: CoverageAuditResult | None) -> tuple[Any, ...] | None:
    if result is None:
        return None
    return (
        result.status,
        tuple(sorted(result.measurements.items())),
        tuple((item.code, item.path, item.message) for item in result.violations),
    )


def _run_g01(project_index: Any, coverage: Any, root: Any) -> CoverageAuditResult | None:
    try:
        return audit_coverage(project_index, coverage, Path(root))
    except Exception:
        return None


def _finish_g01(
    report: dict[str, Any],
    project_index: Any,
    coverage: Any,
    root: Any,
    before: CoverageAuditResult | None,
) -> CoverageAuditResult | None:
    after = _run_g01(project_index, coverage, root)
    report["g01"]["after"] = after.status if after is not None else "FAIL"
    if after is None or after.status == "FAIL":
        _add_code(report, "G01_FAILED")
    if (before is not None and before.status == _PARTIAL) or (
        after is not None and after.status == _PARTIAL
    ):
        _add_code(report, "G01_PARTIAL")
    if _status_signature(before) != _status_signature(after):
        _add_code(report, "SNAPSHOT_DRIFT")
    return after


def _set_base_counts(
    report: dict[str, Any],
    result: CoverageAuditResult | None,
    family_paths: Mapping[str, set[str]],
    tracked: int,
    covered: int,
    outside: int,
) -> None:
    counts = report["counts"]
    if result is not None:
        counts["tracked_files"] = result.measurements["indexed_files"]
        counts["unknown_tracked_files"] = result.measurements["unknown_files"]
        counts["covered_tracked_files"] = max(
            0, counts["tracked_files"] - counts["unknown_tracked_files"],
        )
    else:
        counts["tracked_files"] = tracked
        counts["covered_tracked_files"] = covered
    counts["outside_analyzer_files"] = outside
    for name in _ADAPTERS:
        report["adapters"][name]["applicable_files"] = len(family_paths[name])


def _check_revision_and_metadata(
    report: dict[str, Any],
    artifacts: list[tuple[str, Mapping[str, Any]]],
    project_index: Mapping[str, Any],
    expected_metadata: Mapping[str, Any],
    adapter: str | None = None,
) -> bool:
    valid = True
    revision = project_index.get("repository_revision")
    for _label, artifact in artifacts:
        if artifact.get("repository_revision") != revision:
            _add_code(report, "REVISION_MISMATCH", adapter)
            valid = False
        if artifact.get("source_metadata") != expected_metadata:
            _add_code(report, "SOURCE_METADATA_MISMATCH", adapter)
            valid = False
    return valid


def _has_evidence_collision(evidence_artifacts: list[Mapping[str, Any]]) -> bool:
    registered: dict[str, Mapping[str, Any]] = {}
    conflict = False
    for artifact in evidence_artifacts:
        items = artifact.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
                continue
            identifier = item["id"]
            previous = registered.get(identifier)
            if previous is not None and previous != item:
                conflict = True
            else:
                registered[identifier] = item
    return conflict


def _count_file_statuses(analysis: Mapping[str, Any]) -> tuple[int, int, int, bool]:
    analyzed = skipped = partial = 0
    rows = analysis.get("languages")
    if not isinstance(rows, list):
        raise ValueError("language records are invalid")
    analysis_partial = analysis.get("analysis_status") == _PARTIAL
    for language in rows:
        if not isinstance(language, Mapping) or not isinstance(language.get("files"), list):
            raise ValueError("language file records are invalid")
        analysis_partial = analysis_partial or language.get("analysis_status") == _PARTIAL
        for record in language["files"]:
            if not isinstance(record, Mapping):
                raise ValueError("file record is invalid")
            status = record.get("status")
            if status == "ANALYZED":
                analyzed += 1
            elif status == "SKIPPED":
                skipped += 1
            elif status == "PARTIAL":
                partial += 1
            else:
                raise ValueError("file status is invalid")
    return analyzed, skipped, partial, bool(skipped or partial or analysis_partial)


def _set_adapter_result(
    report: dict[str, Any],
    adapter: str,
    analysis: Mapping[str, Any],
    g01_status: str,
) -> None:
    analyzed, skipped, partial, has_partial_files = _count_file_statuses(analysis)
    row = report["adapters"][adapter]
    row["status"] = _PARTIAL if g01_status == _PARTIAL or has_partial_files else _PASS
    row["version"] = analysis["schema_version"]
    row["analyzed_files"] = analyzed
    row["skipped_files"] = skipped
    row["partial_files"] = partial
    if skipped:
        _add_code(report, "FILES_SKIPPED", adapter)
    if partial:
        _add_code(report, "FILES_PARTIAL", adapter)
    elif has_partial_files and g01_status == _PASS:
        _add_code(report, "ANALYSIS_PARTIAL", adapter)
    if g01_status == _PARTIAL:
        _add_code(report, "G01_PARTIAL")


def _build_snapshot_inputs(
    root: Path,
    project_index: Mapping[str, Any],
):
    context = discover_git_context(root)
    snapshot_kind = project_index["project"]["snapshot_kind"]
    read_source, regular_source_paths = _snapshot_reader(
        root,
        context,
        snapshot_kind,
        project_index,
    )
    if snapshot_kind == "worktree":
        entries = enumerate_git_snapshot_entries(context, "worktree")
        regular_source_paths = frozenset(
            entry.path for entry in entries if entry.mode in {"100644", "100755"}
        )
    return read_source, regular_source_paths


def audit_phase3_bundles(
    *,
    root: str | Path,
    project_index: Mapping[str, Any],
    coverage: Mapping[str, Any],
    stack_profile: Mapping[str, Any],
    phase3a_evidence: Mapping[str, Any],
    python_analysis: Mapping[str, Any] | None = None,
    python_evidence: Mapping[str, Any] | None = None,
    java_analysis_v12: Mapping[str, Any] | None = None,
    java_evidence_v12: Mapping[str, Any] | None = None,
    java_analysis_v13: Mapping[str, Any] | None = None,
    java_evidence_v13: Mapping[str, Any] | None = None,
    frontend_analysis: Mapping[str, Any] | None = None,
    frontend_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Audit explicitly supplied artifacts against one authoritative Git snapshot.

    The return value is an in-memory summary. No source text, artifact paths, or
    validator diagnostics are copied into it, and no files are written.
    """
    report = _new_report()
    family_paths, tracked, covered, outside = _family_paths(project_index, coverage)
    before = _run_g01(project_index, coverage, root)
    report["g01"] = {
        "before": before.status if before is not None else "FAIL",
        "after": _NOT_RUN,
    }
    _set_base_counts(report, before, family_paths, tracked, covered, outside)

    if outside:
        _add_code(report, "OUTSIDE_ANALYZER_SCOPE")
    if before is None or before.status == "FAIL":
        _add_code(report, "G01_FAILED")

    try:
        index, coverage_artifact, profile, original_evidence = _phase2_and_phase3a_inputs(
            project_index, coverage, stack_profile, phase3a_evidence,
        )
    except _AuditInputError as exc:
        _add_code(report, exc.code)
        _finish_g01(report, project_index, coverage, root, before)
        report["status"] = "FAIL"
        return report

    python_pair = _optional_pair(
        report, "python", python_analysis, python_evidence,
        {"1.0.0", "1.1.0"}, "1.1.0",
    )
    java_v12_pair = _optional_pair(
        report, "java", java_analysis_v12, java_evidence_v12,
        {"1.2.0"}, "1.1.0",
    )
    java_v13_pair = _optional_pair(
        report, "java", java_analysis_v13, java_evidence_v13,
        {"1.3.0"}, "1.1.0",
    )
    frontend_pair = _optional_pair(
        report, "frontend", frontend_analysis, frontend_evidence,
        {"1.4.0", "1.5.0"}, "1.1.0",
    )
    has_java_v13 = java_analysis_v13 is not None or java_evidence_v13 is not None
    has_java_v12 = java_analysis_v12 is not None or java_evidence_v12 is not None
    if has_java_v13 and not has_java_v12:
        _add_code(report, "PAIR_INCOMPLETE", "java")

    loaded_pairs = {
        "python": python_pair,
        "java_v12": java_v12_pair,
        "java_v13": java_v13_pair,
        "frontend": frontend_pair,
    }
    if _has_evidence_collision([
        artifact
        for artifact in (
            original_evidence,
            *(pair[1] for pair in loaded_pairs.values() if pair[1] is not None),
        )
    ]):
        _add_code(report, "EVIDENCE_ID_COLLISION")

    if before is None or before.status == "FAIL":
        _finish_g01(report, project_index, coverage, root, before)
        report["status"] = "FAIL"
        return report

    g01_status = before.status
    unknown_files = before.measurements["unknown_files"]
    try:
        metadata = expected_source_metadata(index, coverage_artifact, g01_status, unknown_files)
    except Exception:
        _add_code(report, "SOURCE_METADATA_MISMATCH")
        _finish_g01(report, project_index, coverage, root, before)
        report["status"] = "FAIL"
        return report

    phase3a_valid = _check_revision_and_metadata(
        report,
        [("stack-profile", profile), ("Phase 3A evidence", original_evidence)],
        index,
        metadata,
    )
    try:
        validate_evidence_references(
            profile,
            original_evidence,
            expected_source_metadata=metadata,
        )
    except Exception:
        _add_code(report, "PHASE3A_INVALID")
        phase3a_valid = False
    if not phase3a_valid:
        _add_code(report, "PHASE3A_INVALID")

    supplied_artifacts: dict[str, list[tuple[str, Mapping[str, Any]]]] = {
        "python": [], "java": [], "frontend": [],
    }
    if python_pair[0] is not None:
        supplied_artifacts["python"] = [
            ("analysis", python_pair[0]), ("evidence", python_pair[1]),
        ]
    if java_v12_pair[0] is not None:
        supplied_artifacts["java"].extend([
            ("analysis-v1.2", java_v12_pair[0]), ("evidence-v1.2", java_v12_pair[1]),
        ])
    if java_v13_pair[0] is not None:
        supplied_artifacts["java"].extend([
            ("analysis-v1.3", java_v13_pair[0]), ("evidence-v1.3", java_v13_pair[1]),
        ])
    if frontend_pair[0] is not None:
        supplied_artifacts["frontend"] = [
            ("analysis", frontend_pair[0]), ("evidence", frontend_pair[1]),
        ]

    metadata_valid = {name: True for name in _ADAPTERS}
    for adapter in _ADAPTERS:
        if supplied_artifacts[adapter]:
            metadata_valid[adapter] = _check_revision_and_metadata(
                report, supplied_artifacts[adapter], index, metadata, adapter,
            )

    source_reader = None
    regular_source_paths: frozenset[str] = frozenset()
    if (
        java_v12_pair[0] is not None
        or java_v13_pair[0] is not None
        or frontend_pair[0] is not None
    ):
        try:
            source_reader, regular_source_paths = _build_snapshot_inputs(Path(root), index)
        except Exception:
            adapter = (
                "java"
                if java_v12_pair[0] is not None or java_v13_pair[0] is not None
                else "frontend"
            )
            _add_code(report, "ADAPTER_INVALID", adapter)

    if phase3a_valid:
        if python_pair[0] is not None and metadata_valid["python"]:
            try:
                validate_analysis_bundle(
                    index,
                    coverage_artifact,
                    profile,
                    original_evidence,
                    python_pair[1],
                    python_pair[0],
                    g01_status=g01_status,
                    unknown_files=unknown_files,
                    expected_source_metadata=metadata,
                )
                _set_adapter_result(report, "python", python_pair[0], g01_status)
            except Exception:
                _add_code(report, "ADAPTER_INVALID", "python")

        selected_java_pair = java_v13_pair if java_v13_pair[0] is not None else java_v12_pair
        if selected_java_pair[0] is not None and metadata_valid["java"]:
            java_valid = True
            try:
                validate_java_analysis_bundle(
                    index,
                    coverage_artifact,
                    profile,
                    original_evidence,
                    java_v12_pair[1] if java_v13_pair[0] is not None else selected_java_pair[1],
                    java_v12_pair[0] if java_v13_pair[0] is not None else selected_java_pair[0],
                    g01_status=g01_status,
                    unknown_files=unknown_files,
                    regular_source_paths=regular_source_paths,
                )
            except Exception:
                java_valid = False
            if java_valid and java_v13_pair[0] is not None:
                try:
                    if source_reader is None:
                        raise RepositoryScanError("snapshot reader unavailable")
                    validate_java_framework_bundle(
                        index,
                        coverage_artifact,
                        profile,
                        original_evidence,
                        java_v12_pair[0],
                        java_v12_pair[1],
                        java_v13_pair[0],
                        java_v13_pair[1],
                        source_reader,
                        g01_status=g01_status,
                        unknown_files=unknown_files,
                        regular_source_paths=regular_source_paths,
                    )
                except Exception:
                    java_valid = False
            if java_valid:
                _set_adapter_result(report, "java", selected_java_pair[0], g01_status)
            else:
                _add_code(report, "ADAPTER_INVALID", "java")

        if frontend_pair[0] is not None and metadata_valid["frontend"]:
            try:
                if source_reader is None:
                    source_reader, regular_source_paths = _build_snapshot_inputs(Path(root), index)
                validate_frontend_analysis_bundle(
                    index,
                    coverage_artifact,
                    profile,
                    original_evidence,
                    frontend_pair[1],
                    frontend_pair[0],
                    g01_status=g01_status,
                    unknown_files=unknown_files,
                    regular_source_paths=regular_source_paths,
                    source_reader=source_reader,
                )
                _set_adapter_result(report, "frontend", frontend_pair[0], g01_status)
            except Exception:
                _add_code(report, "ADAPTER_INVALID", "frontend")

    if not phase3a_valid:
        for adapter in _ADAPTERS:
            if supplied_artifacts[adapter]:
                report["adapters"][adapter]["limitations"] = sorted(set(
                    [*report["adapters"][adapter]["limitations"], "PHASE3A_INVALID"],
                ))

    for adapter in _ADAPTERS:
        row = report["adapters"][adapter]
        if row["status"] == _NOT_RUN and family_paths[adapter]:
            if not supplied_artifacts[adapter]:
                _add_code(report, f"{adapter.upper()}_NOT_ANALYZED", adapter)
        counts = report["counts"]
        counts["analyzed_files"] += row["analyzed_files"]
        counts["skipped_files"] += row["skipped_files"]
        counts["partial_files"] += row["partial_files"]
        if row["status"] == _NOT_RUN:
            counts["unreported_source_files"] += row["applicable_files"]

    _finish_g01(report, project_index, coverage, root, before)
    fatal_codes = {
        "INPUT_INVALID", "UNSUPPORTED_VERSION", "VERSION_MISMATCH", "PAIR_INCOMPLETE",
        "G01_FAILED", "SNAPSHOT_DRIFT", "PHASE3A_INVALID", "REVISION_MISMATCH",
        "SOURCE_METADATA_MISMATCH", "EVIDENCE_ID_COLLISION", "ADAPTER_INVALID",
    }
    codes = set(report["limitations"])
    if codes & fatal_codes:
        report["status"] = "FAIL"
    elif (
        report["g01"]["before"] == _PARTIAL
        or report["g01"]["after"] == _PARTIAL
        or any(report["adapters"][name]["status"] == _PARTIAL for name in _ADAPTERS)
    ):
        report["status"] = _PARTIAL
    elif any(
        report["adapters"][name]["status"] == _NOT_RUN and family_paths[name]
        for name in _ADAPTERS
    ):
        report["status"] = _PARTIAL
    else:
        report["status"] = _PASS
    return report


__all__ = ["audit_phase3_bundles"]
