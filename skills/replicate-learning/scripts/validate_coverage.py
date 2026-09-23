#!/usr/bin/env python3
"""Audit Project DeepDive project-index.json and coverage.json against each other.

Output is concise and machine-friendly: one status line with counts and one line
per violation.  It does not emit a Phase 9 ``quality-report.json`` early.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from artifact_contract import ArtifactValidationError, load_artifact
from coverage_audit import audit_coverage
from repository_scan import RepositoryScanError

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit project-index.json and coverage.json for cross-artifact coverage integrity",
    )
    parser.add_argument("--project-index", required=True, type=Path, help="project-index.json path")
    parser.add_argument("--coverage", required=True, type=Path, help="coverage.json path")
    parser.add_argument("--root", required=True, type=Path, help="analysed root the artifacts describe")
    parser.add_argument("--require-complete", action="store_true",
                        help="fail when any tracked file is still UNKNOWN")
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    for path in (args.project_index, args.coverage):
        if not path.is_file():
            print(f"validate_coverage.py: error: artifact does not exist: {path}", file=sys.stderr)
            return EXIT_USAGE

    try:
        project_index = load_artifact(args.project_index)
        coverage = load_artifact(args.coverage)
    except ArtifactValidationError as exc:
        print("status=FAIL indexed_files=0 coverage_entries=0 unknown_files=0")
        print(f"violation SCHEMA_INVALID {args.coverage}: {exc}")
        return EXIT_FAILURE

    try:
        result = audit_coverage(project_index, coverage, args.root,
                                require_complete=args.require_complete)
    except RepositoryScanError as exc:
        print(f"validate_coverage.py: error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    measurements = result.measurements
    print(f"status={result.status} indexed_files={measurements['indexed_files']} "
          f"coverage_entries={measurements['coverage_entries']} "
          f"unknown_files={measurements['unknown_files']}")
    for violation in result.violations:
        print(f"violation {violation.code} {violation.path}: {violation.message}")
    return EXIT_FAILURE if result.status == "FAIL" else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
