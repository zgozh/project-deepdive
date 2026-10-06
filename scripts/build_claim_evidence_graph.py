#!/usr/bin/env python3
"""Build an authenticated compact Claim/Evidence graph overlay."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase4_claim_evidence import Phase4ClaimEvidenceError, build_claim_evidence_graph_4c


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a source-authenticated overlay from an exact Phase 4E1 claim package."
    )
    parser.add_argument("--phase4c-package", required=True, type=Path)
    parser.add_argument("--claims", required=True, type=Path)
    parser.add_argument("--audit-report", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        artifact = build_claim_evidence_graph_4c(
            args.claims,
            args.audit_report,
            package_dir=args.phase4c_package,
            run_dir=args.run_dir,
            root=args.root,
            out=args.out,
        )
    except Phase4ClaimEvidenceError as exc:
        print(f"error={exc.code}")
        return 2

    claim_count = sum(node["type"] == "Claim" for node in artifact["nodes"])
    evidence_count = sum(node["type"] == "Evidence" for node in artifact["nodes"])
    print(
        " ".join((
            f"status={artifact['audit_status']}",
            f"claims={claim_count}",
            f"evidence={evidence_count}",
            f"edges={len(artifact['edges'])}",
        ))
    )
    return 1 if artifact["audit_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
