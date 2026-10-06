#!/usr/bin/env python3
"""Command-line entry point for the Phase 4A graph/evidence builder."""

from __future__ import annotations

import argparse
from pathlib import Path

from phase4_graph import Phase4GraphError, build_phase4_graph


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a deterministic Phase 4 graph and evidence fold from a validated Phase 3 run",
    )
    parser.add_argument("--run-dir", required=True, type=Path, help="validated phase3-run directory")
    parser.add_argument("--root", required=True, type=Path, help="matching target repository root")
    parser.add_argument("--out", required=True, type=Path, help="new output directory outside the target")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        counts = build_phase4_graph(args.run_dir, root=args.root, out=args.out)
    except Phase4GraphError as exc:
        print(f"status=FAIL code={exc.code}")
        return 1
    except Exception:
        print("status=FAIL code=BUILD_FAILED")
        return 1
    print(
        f"status={counts['status']} nodes={counts['nodes']} edges={counts['edges']} "
        f"files={counts['files']} symbols={counts['symbols']} relations={counts['relations']} "
        f"roles={counts['roles']} evidence={counts['evidence']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
