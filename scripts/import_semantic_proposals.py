#!/usr/bin/env python3
"""CLI for source-backed Phase 4C provisional semantic proposals."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase4_proposals import Phase4ProposalError, import_semantic_proposals


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import provisional proposals into a source-backed Phase 4 graph.")
    parser.add_argument("--proposals", required=True, type=Path)
    parser.add_argument("--graph", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = import_semantic_proposals(
            args.proposals,
            graph_path=args.graph,
            evidence_path=args.evidence,
            run_dir=args.run_dir,
            root=args.root,
            out=args.out,
        )
    except Phase4ProposalError as exc:
        print(f"error={exc.code}")
        return 2

    print(
        " ".join((
            f"status={summary['status']}",
            f"proposals={summary['proposals']}",
            f"nodes={summary['nodes']}",
            f"edges={summary['edges']}",
            f"evidence={summary['evidence']}",
        ))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
