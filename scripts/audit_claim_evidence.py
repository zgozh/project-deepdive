#!/usr/bin/env python3
"""CLI for source-backed Phase 4B claim auditing."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase4_claim_evidence import (
    Phase4ClaimEvidenceError,
    audit_claim_candidates,
    publish_claim_evidence,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit claim references against a source-backed Phase 4A pair.")
    parser.add_argument("--claims", required=True, type=Path)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_claim_candidates(
            args.claims,
            graph_path=args.graph,
            evidence_path=args.evidence,
            run_dir=args.run_dir,
            root=args.root,
        )
        publish_claim_evidence(report, root=args.root, out=args.out)
    except Phase4ClaimEvidenceError as exc:
        print(f"error={exc.code}")
        return 2

    counts = {disposition: 0 for disposition in ("SUPPORTED", "INFERENCE", "UNVERIFIED")}
    for claim in report["claims"]:
        counts[claim["disposition"]] += 1
    print(
        " ".join((
            f"status={report['audit_status']}",
            f"claims={len(report['claims'])}",
            f"supported={counts['SUPPORTED']}",
            f"inference={counts['INFERENCE']}",
            f"unverified={counts['UNVERIFIED']}",
        ))
    )
    return 1 if report["audit_status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
