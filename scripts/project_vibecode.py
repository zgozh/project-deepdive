#!/usr/bin/env python3
"""Compile static author inputs; never execute commands or target-project code."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from artifact_contract import ArtifactValidationError, dumps_artifact, load_artifact
from phase7_vibecoding import render_lab, render_prompts, validate_development_plan


def compile_plan_files(plan_path: str | Path, index_path: str | Path,
                       evidence_path: str | Path, out: str | Path) -> Path:
    plan = load_artifact(plan_path)
    index = load_artifact(index_path)
    evidence = load_artifact(evidence_path)
    validate_development_plan(plan, index, evidence)
    output = Path(out).absolute()
    target = Path(index["project"]["root"]).resolve()
    if output.resolve().is_relative_to(target):
        raise ArtifactValidationError("output must be outside declared target project root")
    texts = (("LAB.md", render_lab(plan, evidence)),
             ("PROMPTS.md", render_prompts(plan)),
             ("development-plan.json", dumps_artifact(plan)))
    # mkdir without exist_ok rejects existing directories, files and dangling links.
    # The parent must already exist; input failure never creates an output directory.
    output.mkdir()
    identity = output.lstat()
    created: list[tuple[Path, os.stat_result]] = []
    try:
        for name, content in texts:
            path = output / name
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                created.append((path, os.fstat(handle.fileno())))
                handle.write(content)
    except BaseException:
        # Remove only entries this invocation created, while the owned directory remains.
        try:
            current = output.lstat()
            if (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino):
                for path, own in reversed(created):
                    try:
                        stat = path.lstat()
                        if (stat.st_dev, stat.st_ino) == (own.st_dev, own.st_ino):
                            path.unlink()
                    except OSError:
                        pass
                output.rmdir()  # Never recursively delete an unexpected external entry.
        except OSError:
            pass
        raise
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("plan", "project-index", "evidence", "out"):
        parser.add_argument("--" + flag, required=True)
    args = parser.parse_args(argv)
    try:
        output = compile_plan_files(args.plan, args.project_index, args.evidence, args.out)
    except (ArtifactValidationError, OSError) as exc:
        print(f"project-vibecode: {exc}", file=sys.stderr)
        return 2
    print(f"Prepared static author plan: {output}; implementation/verification NOT_RUN; review PENDING")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
