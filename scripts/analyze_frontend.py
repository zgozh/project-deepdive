#!/usr/bin/env python3
"""Emit a local parse-only report for explicitly supplied JavaScript/TypeScript files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from frontend_static_analysis import FrontendAnalysisError, analyze_paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse explicitly supplied JavaScript/TypeScript source without loading project code",
    )
    parser.add_argument("--root", required=True, type=Path, help="source root for explicit relative file paths")
    parser.add_argument(
        "--file", action="append", required=True, metavar="PATH",
        help="explicit .js, .jsx, .ts, or .tsx path under --root; repeat for more files",
    )
    parser.add_argument(
        "--protocol-version", choices=("1", "2"), default="1",
        help="parser protocol (1 preserves the original report; 2 opts into frontend candidates)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        report = analyze_paths(
            args.root,
            args.file,
            protocol_version=int(args.protocol_version),
        )
    except FrontendAnalysisError as exc:
        print(f"analyze_frontend.py: error: {exc.code}", file=sys.stderr)
        return 2
    except Exception:
        print("analyze_frontend.py: error: ANALYSIS_FAILED", file=sys.stderr)
        return 2
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
