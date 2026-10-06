#!/usr/bin/env python3
"""Publish a bounded Java parse-only static-analysis bundle from one Git snapshot."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from artifact_contract import (
    ArtifactValidationError,
    _strict_json_loads,
    dumps_artifact,
    load_artifact,
    validate_artifact,
)
from coverage_audit import audit_coverage
from detect_stack import _snapshot_reader
from java_static_analysis import StaticAnalysisError, analyze_java_artifacts
from repository_scan import (
    RepositoryScanError,
    discover_git_context,
    enumerate_git_snapshot_entries,
)
from scan_repository import _assert_publishable, _publish, configure_stdio
from stack_detection import StackDetectionError


EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_OPERATIONAL = 2
_OUTPUT_NAMES = ("stack-profile.json", "evidence.json", "static-analysis.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract bounded JDK parse-only Java facts from an audited Git snapshot",
    )
    parser.add_argument("--root", required=True, type=Path, help="analysed Git repository or subtree")
    parser.add_argument("--index", required=True, type=Path, help="Phase 2 project-index.json")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage.json")
    parser.add_argument("--stack-profile", required=True, type=Path, help="Phase 3A stack-profile.json")
    parser.add_argument("--evidence", required=True, type=Path, help="Phase 3A evidence.json")
    parser.add_argument("--out", required=True, type=Path, help="directory for the merged Java analysis bundle")
    return parser


def _print_audit(result) -> None:
    measurements = result.measurements
    print(f"status={result.status} indexed_files={measurements['indexed_files']} "
          f"coverage_entries={measurements['coverage_entries']} "
          f"unknown_files={measurements['unknown_files']}")
    for violation in result.violations:
        print(f"violation {violation.code} {violation.path}: {violation.message}")


def _assert_outputs_separate(out: Path, inputs: tuple[Path, ...], tracked_paths: set[Path]) -> None:
    _assert_publishable(out, _OUTPUT_NAMES, tracked_paths, None)
    input_paths = {Path(path).resolve() for path in inputs}
    for name in _OUTPUT_NAMES:
        output = (out / name).resolve()
        if output in input_paths:
            raise RepositoryScanError(f"--out would overwrite an input artifact: {output}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    artifact_paths = (
        ("project-index", args.index),
        ("coverage", args.coverage),
        ("stack-profile", args.stack_profile),
        ("evidence", args.evidence),
    )
    for label, path in artifact_paths:
        if not path.is_file():
            print(f"analyze_java.py: error: {label} input does not exist or is not a file: {path}", file=sys.stderr)
            return EXIT_OPERATIONAL

    try:
        project_index = load_artifact(args.index)
        coverage = load_artifact(args.coverage)
        stack_profile_bytes = args.stack_profile.read_bytes()
        stack_profile = validate_artifact(
            _strict_json_loads(stack_profile_bytes.decode("utf-8")),
        )
        evidence = load_artifact(args.evidence)
    except ArtifactValidationError as exc:
        if isinstance(exc.__cause__, OSError):
            print("analyze_java.py: error: cannot read an input artifact", file=sys.stderr)
            return EXIT_OPERATIONAL
        print(f"status=FAIL violation SCHEMA_INVALID: {exc}")
        return EXIT_FAILURE
    except (UnicodeError, ValueError) as exc:
        print(f"status=FAIL violation SCHEMA_INVALID: cannot parse an input artifact: {exc}")
        return EXIT_FAILURE
    except OSError as exc:
        print(f"analyze_java.py: error: cannot read an input artifact: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    root = Path(args.root).resolve()
    try:
        audit = audit_coverage(project_index, coverage, root)
    except RepositoryScanError as exc:
        print(f"analyze_java.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    _print_audit(audit)
    if audit.status == "FAIL":
        return EXIT_FAILURE

    try:
        expected_status = audit.status
        unknown_files = audit.measurements["unknown_files"]
        context = discover_git_context(root)
        tracked_paths = {
            (root / entry["path"]).resolve() for entry in project_index["files"]
        }
        _assert_outputs_separate(
            args.out,
            (args.index, args.coverage, args.stack_profile, args.evidence),
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
        static_analysis, merged_evidence = analyze_java_artifacts(
            project_index,
            coverage,
            stack_profile,
            evidence,
            read_source,
            regular_source_paths,
            g01_status=expected_status,
            unknown_files=unknown_files,
        )
    except (StaticAnalysisError, StackDetectionError) as exc:
        print(f"analyze_java.py: validation failed: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except (RepositoryScanError, OSError) as exc:
        print(f"analyze_java.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        final_audit = audit_coverage(project_index, coverage, root)
    except RepositoryScanError as exc:
        print(f"analyze_java.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    if final_audit.status == "FAIL":
        _print_audit(final_audit)
        print("analyze_java.py: error: Phase 2 snapshot changed before publication", file=sys.stderr)
        return EXIT_FAILURE

    try:
        _publish(args.out, {
            _OUTPUT_NAMES[0]: stack_profile_bytes.decode("utf-8"),
            _OUTPUT_NAMES[1]: dumps_artifact(merged_evidence),
            _OUTPUT_NAMES[2]: dumps_artifact(static_analysis),
        })
    except (OSError, UnicodeError) as exc:
        print(f"analyze_java.py: error: cannot publish static-analysis bundle: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    file_records = static_analysis["languages"][0]["files"]
    analyzed = sum(item["status"] == "ANALYZED" for item in file_records)
    skipped = sum(item["status"] == "SKIPPED" for item in file_records)
    print(
        "published stack-profile.json, evidence.json and static-analysis.json; "
        f"java_files={len(file_records)} analyzed={analyzed} skipped={skipped} "
        f"symbols={len(static_analysis['symbols'])} relations={len(static_analysis['relations'])} "
        f"snapshot={static_analysis['source_metadata']['snapshot_kind']} "
        f"analysis_status={static_analysis['analysis_status']} schema_version=1.2.0"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
