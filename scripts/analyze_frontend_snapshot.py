#!/usr/bin/env python3
"""Publish versioned frontend static-analysis from an audited Git snapshot."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from artifact_contract import (
    ArtifactValidationError,
    _strict_json_loads,
    dumps_artifact,
    validate_artifact,
)
from coverage_audit import audit_coverage
from detect_stack import _snapshot_reader
from frontend_snapshot_analysis import (
    FrontendSnapshotAnalysisError,
    analyze_frontend_artifacts,
)
from repository_scan import (
    RepositoryScanError,
    discover_git_context,
    enumerate_git_snapshot_entries,
)
from scan_repository import (
    _assert_publishable,
    _publish,
    configure_stdio,
)
from stack_detection import StackDetectionError


EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_OPERATIONAL = 2
OUTPUT_NAMES = ("stack-profile.json", "static-analysis.json", "evidence.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish opt-in frontend static-analysis facts from a validated Git snapshot",
    )
    parser.add_argument("--root", required=True, type=Path, help="analysed Git repository or subtree")
    parser.add_argument("--index", required=True, type=Path, help="Phase 2 project-index.json")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage.json")
    parser.add_argument("--stack-profile", required=True, type=Path, help="Phase 3A stack-profile v1.1")
    parser.add_argument("--phase3a-evidence", required=True, type=Path, help="Phase 3A evidence v1.1")
    parser.add_argument("--out", required=True, type=Path, help="directory for the three frontend artifacts")
    parser.add_argument(
        "--analysis-version", choices=("1.4.0", "1.5.0"), default="1.4.0",
        help="frontend static-analysis contract (default: 1.4.0)",
    )
    return parser


def _read_artifact(path: Path) -> tuple[bytes, dict]:
    try:
        raw = path.read_bytes()
        value = _strict_json_loads(raw.decode("utf-8", errors="strict"))
        validate_artifact(value)
    except (ArtifactValidationError, OSError, UnicodeError, ValueError, TypeError) as exc:
        raise FrontendSnapshotAnalysisError("INPUT_INVALID") from exc
    if not isinstance(value, dict):
        raise FrontendSnapshotAnalysisError("INPUT_INVALID")
    return raw, value


def _assert_outputs_separate(
    out: Path,
    inputs: tuple[Path, ...],
    tracked_paths: set[Path],
) -> None:
    _assert_publishable(out, OUTPUT_NAMES, tracked_paths, None)
    input_paths = {Path(path).resolve() for path in inputs}
    if any((out / name).resolve() in input_paths for name in OUTPUT_NAMES):
        raise RepositoryScanError("--out would overwrite an input artifact")


def _print_audit(result) -> None:
    measurements = result.measurements
    print(
        f"status={result.status} indexed_files={measurements['indexed_files']} "
        f"coverage_entries={measurements['coverage_entries']} "
        f"unknown_files={measurements['unknown_files']}"
    )


def _verify_read_sources(analysis, files_by_path, source_reader) -> None:
    for language in analysis["languages"]:
        for record in language["files"]:
            was_read = record["status"] in {"ANALYZED", "PARTIAL"} or any(
                item["code"] == "UNSUPPORTED_ENCODING"
                for item in record["limitations"]
            )
            if not was_read:
                continue
            path = record["path"]
            entry = files_by_path[path]
            try:
                payload = source_reader(path, entry)
            except Exception as exc:
                raise FrontendSnapshotAnalysisError("SOURCE_RECHECK_FAILED") from exc
            if (
                not isinstance(payload, bytes)
                or len(payload) != entry["bytes"]
                or hashlib.sha256(payload).hexdigest() != record["sha256"]
            ):
                raise FrontendSnapshotAnalysisError("SOURCE_DRIFT")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    artifact_inputs = (
        ("project-index", args.index),
        ("coverage", args.coverage),
        ("stack-profile", args.stack_profile),
        ("Phase 3A evidence", args.phase3a_evidence),
    )
    for label, path in artifact_inputs:
        if not path.is_file():
            print(
                f"analyze_frontend_snapshot.py: error: {label} input is missing",
                file=sys.stderr,
            )
            return EXIT_OPERATIONAL

    try:
        index_bytes, project_index = _read_artifact(args.index)
        coverage_bytes, coverage = _read_artifact(args.coverage)
        stack_profile_bytes, stack_profile = _read_artifact(args.stack_profile)
        evidence_bytes, phase3a_evidence = _read_artifact(args.phase3a_evidence)
        del index_bytes, coverage_bytes, evidence_bytes
        if (
            project_index.get("artifact_kind") != "project-index"
            or coverage.get("artifact_kind") != "coverage"
            or stack_profile.get("artifact_kind") != "stack-profile"
            or stack_profile.get("schema_version") != "1.1.0"
            or phase3a_evidence.get("artifact_kind") != "evidence"
            or phase3a_evidence.get("schema_version") != "1.1.0"
        ):
            raise FrontendSnapshotAnalysisError("INPUT_VERSION_UNSUPPORTED")
    except FrontendSnapshotAnalysisError as exc:
        print(f"status=FAIL error={exc.code}")
        return EXIT_FAILURE

    root = Path(args.root).resolve()
    try:
        audit = audit_coverage(project_index, coverage, root)
    except (RepositoryScanError, OSError):
        print("analyze_frontend_snapshot.py: error: SNAPSHOT_AUDIT_FAILED", file=sys.stderr)
        return EXIT_OPERATIONAL
    _print_audit(audit)
    if audit.status == "FAIL":
        return EXIT_FAILURE

    expected_status = audit.status
    unknown_files = audit.measurements["unknown_files"]
    try:
        context = discover_git_context(root)
        tracked_paths = {
            (root / entry["path"]).resolve()
            for entry in project_index["files"]
        }
        _assert_outputs_separate(
            args.out,
            tuple(path for _label, path in artifact_inputs),
            tracked_paths,
        )
        read_source, regular_source_paths = _snapshot_reader(
            root,
            context,
            project_index["project"]["snapshot_kind"],
            project_index,
        )
        if project_index["project"]["snapshot_kind"] == "worktree":
            entries = enumerate_git_snapshot_entries(context, "worktree")
            regular_source_paths = frozenset(
                entry.path for entry in entries if entry.mode in {"100644", "100755"}
            )
        analysis, merged_evidence = analyze_frontend_artifacts(
            project_index,
            coverage,
            stack_profile,
            phase3a_evidence,
            read_source,
            regular_source_paths,
            g01_status=expected_status,
            unknown_files=unknown_files,
            analysis_version=args.analysis_version,
        )
    except FrontendSnapshotAnalysisError as exc:
        print(f"status=FAIL error={exc.code}")
        return EXIT_FAILURE
    except (RepositoryScanError, StackDetectionError, OSError):
        print("analyze_frontend_snapshot.py: error: SNAPSHOT_ANALYSIS_FAILED", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        final_audit = audit_coverage(project_index, coverage, root)
        if (
            final_audit.status != expected_status
            or final_audit.measurements["unknown_files"] != unknown_files
        ):
            raise FrontendSnapshotAnalysisError("SNAPSHOT_STATUS_CHANGED")
        files_by_path = {entry["path"]: entry for entry in project_index["files"]}
        _verify_read_sources(analysis, files_by_path, read_source)
    except FrontendSnapshotAnalysisError as exc:
        print(f"status=FAIL error={exc.code}")
        return EXIT_FAILURE
    except (RepositoryScanError, StackDetectionError, OSError):
        print("analyze_frontend_snapshot.py: error: SNAPSHOT_RECHECK_FAILED", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        _publish(args.out, {
            OUTPUT_NAMES[0]: stack_profile_bytes.decode("utf-8", errors="strict"),
            OUTPUT_NAMES[1]: dumps_artifact(analysis),
            OUTPUT_NAMES[2]: dumps_artifact(merged_evidence),
        })
    except (ArtifactValidationError, FrontendSnapshotAnalysisError) as exc:
        print(f"status=FAIL error={getattr(exc, 'code', 'OUTPUT_INVALID')}")
        return EXIT_FAILURE
    except (OSError, UnicodeError):
        print("analyze_frontend_snapshot.py: error: PUBLICATION_FAILED", file=sys.stderr)
        return EXIT_OPERATIONAL

    symbols = len(analysis["symbols"])
    relations = len(analysis["relations"])
    roles = len(analysis["roles"])
    file_count = sum(len(item["files"]) for item in analysis["languages"])
    print(
        "published frontend snapshot artifacts; "
        f"files={file_count} symbols={symbols} relations={relations} roles={roles} "
        f"snapshot={analysis['source_metadata']['snapshot_kind']} "
        f"analysis_status={analysis['analysis_status']} schema_version={analysis['schema_version']}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
