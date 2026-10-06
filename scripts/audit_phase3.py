#!/usr/bin/env python3
"""Print a redacted summary for an explicit Phase 3 bundle audit."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from artifact_contract import load_artifact
from phase3_bundle_audit import audit_phase3_bundles


EXIT_OK = 0
EXIT_FAILURE = 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only audit of explicit Phase 2, Phase 3A, and optional "
            "language analysis bundles"
        ),
    )
    parser.add_argument("--root", required=True, type=Path, help="audited Git repository")
    parser.add_argument("--project-index", required=True, type=Path, help="Phase 2 project-index")
    parser.add_argument("--coverage", required=True, type=Path, help="Phase 2 coverage")
    parser.add_argument("--stack-profile", required=True, type=Path, help="Phase 3A stack-profile v1.1")
    parser.add_argument("--phase3a-evidence", required=True, type=Path, help="Phase 3A evidence v1.1")
    parser.add_argument("--python-analysis", type=Path, help="Python static-analysis v1.0 or v1.1")
    parser.add_argument("--python-evidence", type=Path, help="evidence paired with Python analysis")
    parser.add_argument("--java-analysis-v12", type=Path, help="Java static-analysis v1.2")
    parser.add_argument("--java-evidence-v12", type=Path, help="evidence paired with Java v1.2")
    parser.add_argument("--java-analysis-v13", type=Path, help="Java static-analysis v1.3")
    parser.add_argument("--java-evidence-v13", type=Path, help="evidence paired with Java v1.3")
    parser.add_argument("--frontend-analysis", type=Path, help="frontend static-analysis v1.4 or v1.5")
    parser.add_argument("--frontend-evidence", type=Path, help="evidence paired with frontend analysis")
    return parser


def _load_optional(path: Path | None):
    return load_artifact(path) if path is not None else None


def _summary(report: dict) -> str:
    counts = report["counts"]
    adapters = report["adapters"]
    first = (
        f"status={report['status']} g01={report['g01']['before']}/{report['g01']['after']} "
        f"tracked={counts['tracked_files']} covered={counts['covered_tracked_files']} "
        f"unknown={counts['unknown_tracked_files']} analyzed={counts['analyzed_files']} "
        f"skipped={counts['skipped_files']} partial={counts['partial_files']} "
        f"unreported={counts['unreported_source_files']} outside={counts['outside_analyzer_files']}"
    )
    second = "adapters=" + ",".join(
        f"{name}:{adapters[name]['status']}" for name in ("python", "java", "frontend")
    )
    limitations = "limitations=" + ",".join(report["limitations"])
    return f"{first}\n{second}\n{limitations}"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = audit_phase3_bundles(
            root=args.root,
            project_index=load_artifact(args.project_index),
            coverage=load_artifact(args.coverage),
            stack_profile=load_artifact(args.stack_profile),
            phase3a_evidence=load_artifact(args.phase3a_evidence),
            python_analysis=_load_optional(args.python_analysis),
            python_evidence=_load_optional(args.python_evidence),
            java_analysis_v12=_load_optional(args.java_analysis_v12),
            java_evidence_v12=_load_optional(args.java_evidence_v12),
            java_analysis_v13=_load_optional(args.java_analysis_v13),
            java_evidence_v13=_load_optional(args.java_evidence_v13),
            frontend_analysis=_load_optional(args.frontend_analysis),
            frontend_evidence=_load_optional(args.frontend_evidence),
        )
    except Exception:
        # Input paths and parser diagnostics are intentionally not echoed.
        print("status=FAIL\nlimitations=INPUT_INVALID")
        return EXIT_FAILURE

    print(_summary(report))
    return EXIT_FAILURE if report["status"] == "FAIL" else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
