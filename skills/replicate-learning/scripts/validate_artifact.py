#!/usr/bin/env python3
"""Validate one or more persisted Project DeepDive JSON artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from artifact_contract import ArtifactValidationError, load_artifact


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(
        description="Validate Project DeepDive artifacts against their versioned schemas"
    )
    parser.add_argument("artifacts", nargs="+", type=Path, help="UTF-8 JSON artifact path")
    args = parser.parse_args(argv)

    failed = False
    for path in args.artifacts:
        try:
            artifact = load_artifact(path)
        except ArtifactValidationError as exc:
            failed = True
            print(f"FAIL {path}: {exc}")
            continue
        print(f"PASS {path} ({artifact['artifact_kind']} v{artifact['schema_version']})")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
