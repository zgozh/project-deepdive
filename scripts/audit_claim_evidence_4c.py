#!/usr/bin/env python3
"""CLI for source-authenticated Phase 4C claim auditing."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase4_claim_evidence import (
    Phase4ClaimEvidenceError,
    audit_and_publish_claim_evidence_4c,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit claims against the exact Phase 4C package selected by claim schema version.",
    )
    parser.add_argument("--claims", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = audit_and_publish_claim_evidence_4c(
            args.claims,
            package_dir=args.package,
            run_dir=args.run_dir,
            root=args.root,
            out=args.out,
        )
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
