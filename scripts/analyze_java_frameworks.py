#!/usr/bin/env python3
"""Publish parse-only Java framework candidates from an audited Git snapshot."""

from __future__ import annotations

import argparse
import hashlib
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
from java_framework_static_analysis import (
    StaticAnalysisError,
    _build_framework_candidates,
    compare_supplied_v12_bundle,
    rebuild_v12_bundle,
)
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
_OUTPUT_NAMES = (
    "stack-profile.json",
    "static-analysis-v1.2.json",
    "evidence-v1.2.json",
    "static-analysis.json",
    "evidence.json",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract allowlisted Java framework candidates from an audited Git snapshot",
    )
    parser.add_argument("--root", required=True, type=Path, help="analysed Git repository or subtree")
    parser.add_argument("--index", required=True, type=Path, help="Phase 2 project-index.json")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage.json")
    parser.add_argument("--stack-profile", required=True, type=Path, help="Phase 3A stack-profile v1.1")
    parser.add_argument("--phase3a-evidence", required=True, type=Path, help="Phase 3A evidence v1.1")
    parser.add_argument("--java-analysis-v1.2", dest="java_analysis_v12", required=True, type=Path,
                        help="supplied 3C1 Java static-analysis v1.2")
    parser.add_argument("--java-evidence-v1.2", dest="java_evidence_v12", required=True, type=Path,
                        help="supplied evidence paired with Java v1.2")
    parser.add_argument("--out", required=True, type=Path, help="directory for the five-artifact C2 bundle")
    return parser


def _read_profile(path: Path):
    raw = path.read_bytes()
    try:
        value = validate_artifact(_strict_json_loads(raw.decode("utf-8")))
    except (ArtifactValidationError, UnicodeError, ValueError) as exc:
        raise StaticAnalysisError("Phase 3A stack-profile input is invalid") from exc
    return raw, value


def _assert_outputs_separate(out: Path, inputs: tuple[Path, ...], tracked_paths: set[Path]) -> None:
    _assert_publishable(out, _OUTPUT_NAMES, tracked_paths, None)
    input_paths = {Path(path).resolve() for path in inputs}
    for name in _OUTPUT_NAMES:
        if (out / name).resolve() in input_paths:
            raise RepositoryScanError("--out would overwrite an input artifact")


def _print_audit(result) -> None:
    measurements = result.measurements
    print(f"status={result.status} indexed_files={measurements['indexed_files']} "
          f"coverage_entries={measurements['coverage_entries']} "
          f"unknown_files={measurements['unknown_files']}")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    args = build_parser().parse_args(argv)
    artifact_inputs = (
        ("project-index", args.index),
        ("coverage", args.coverage),
        ("stack-profile", args.stack_profile),
        ("Phase 3A evidence", args.phase3a_evidence),
        ("Java v1.2 analysis", args.java_analysis_v12),
        ("Java v1.2 evidence", args.java_evidence_v12),
    )
    for label, path in artifact_inputs:
        if not path.is_file():
            print(f"analyze_java_frameworks.py: error: {label} input does not exist or is not a file", file=sys.stderr)
            return EXIT_OPERATIONAL

    try:
        project_index = load_artifact(args.index)
        coverage = load_artifact(args.coverage)
        stack_profile_bytes, stack_profile = _read_profile(args.stack_profile)
        phase3a_evidence = load_artifact(args.phase3a_evidence)
        supplied_v12_analysis = load_artifact(args.java_analysis_v12)
        supplied_v12_evidence = load_artifact(args.java_evidence_v12)
        for artifact, kind, version in (
            (project_index, "project-index", None),
            (coverage, "coverage", None),
            (stack_profile, "stack-profile", "1.1.0"),
            (phase3a_evidence, "evidence", "1.1.0"),
            (supplied_v12_analysis, "static-analysis", "1.2.0"),
            (supplied_v12_evidence, "evidence", "1.1.0"),
        ):
            if artifact.get("artifact_kind") != kind or (version and artifact.get("schema_version") != version):
                raise StaticAnalysisError("input artifact kind or schema version is outside the C2 contract")
    except ArtifactValidationError:
        print("status=FAIL violation INPUT_INVALID: input artifact failed schema validation")
        return EXIT_FAILURE
    except StaticAnalysisError as exc:
        print(f"status=FAIL violation INPUT_INVALID: {exc}")
        return EXIT_FAILURE
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"analyze_java_frameworks.py: error: cannot read an input artifact: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    root = Path(args.root).resolve()
    try:
        audit = audit_coverage(project_index, coverage, root)
    except RepositoryScanError as exc:
        print(f"analyze_java_frameworks.py: error: {exc}", file=sys.stderr)
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
            tuple(path for _label, path in artifact_inputs),
            tracked_paths,
        )
        read_source, regular_source_paths = _snapshot_reader(
            root, context, project_index["project"]["snapshot_kind"], project_index,
        )
        if project_index["project"]["snapshot_kind"] == "worktree":
            entries = enumerate_git_snapshot_entries(context, "worktree")
            regular_source_paths = frozenset(
                entry.path for entry in entries if entry.mode in {"100644", "100755"}
            )

        rebuilt_v12_analysis, rebuilt_v12_evidence = rebuild_v12_bundle(
            project_index, coverage, stack_profile, phase3a_evidence,
            read_source, regular_source_paths,
            g01_status=expected_status, unknown_files=unknown_files,
        )
        compare_supplied_v12_bundle(
            rebuilt_v12_analysis, rebuilt_v12_evidence,
            supplied_v12_analysis, supplied_v12_evidence,
        )
        framework_analysis, final_evidence = _build_framework_candidates(
            project_index, coverage, stack_profile, phase3a_evidence,
            rebuilt_v12_analysis, rebuilt_v12_evidence,
            read_source, regular_source_paths,
            g01_status=expected_status, unknown_files=unknown_files,
        )
    except (StaticAnalysisError, StackDetectionError) as exc:
        print(f"analyze_java_frameworks.py: validation failed: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except (RepositoryScanError, OSError) as exc:
        print(f"analyze_java_frameworks.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    try:
        final_audit = audit_coverage(project_index, coverage, root)
        if (final_audit.status != expected_status
                or final_audit.measurements["unknown_files"] != unknown_files):
            raise StaticAnalysisError("Phase 2 snapshot status changed before publication")
        entries_by_path = {entry["path"]: entry for entry in project_index["files"]}
        for file_record in rebuilt_v12_analysis["languages"][0]["files"]:
            if file_record["status"] != "ANALYZED":
                continue
            entry = entries_by_path[file_record["path"]]
            payload = read_source(file_record["path"], entry)
            if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise StaticAnalysisError("selected source changed before publication")
    except (RepositoryScanError, OSError) as exc:
        print(f"analyze_java_frameworks.py: error: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL
    except StaticAnalysisError as exc:
        print(f"analyze_java_frameworks.py: validation failed: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        _publish(args.out, {
            _OUTPUT_NAMES[0]: stack_profile_bytes.decode("utf-8"),
            _OUTPUT_NAMES[1]: dumps_artifact(rebuilt_v12_analysis),
            _OUTPUT_NAMES[2]: dumps_artifact(rebuilt_v12_evidence),
            _OUTPUT_NAMES[3]: dumps_artifact(framework_analysis),
            _OUTPUT_NAMES[4]: dumps_artifact(final_evidence),
        })
    except (OSError, UnicodeError) as exc:
        print(f"analyze_java_frameworks.py: error: cannot publish C2 bundle: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL

    route_count = sum(row["kind"] == "ROUTE_TO" for row in framework_analysis["relations"])
    print(
        "published five Phase 3C2 artifacts; "
        f"java_files={len(framework_analysis['languages'][0]['files'])} "
        f"symbols={len(framework_analysis['symbols'])} "
        f"route_candidates={route_count} role_candidates={len(framework_analysis['roles'])} "
        f"snapshot={framework_analysis['source_metadata']['snapshot_kind']} "
        f"analysis_status={framework_analysis['analysis_status']} schema_version=1.3.0"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
