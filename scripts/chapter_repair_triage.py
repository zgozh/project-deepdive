#!/usr/bin/env python3
"""Authenticate Phase 6B1 findings and publish a deterministic B2a handoff."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from artifact_contract import dumps_artifact
from chapter_review_workflow import (
    _add_inputs,
    _ensure_separate,
    _paths_from_args,
    authenticate_chapter_review_package,
)
from phase6_chapter import (
    Phase6ChapterError,
    _absolute,
    _check_external_output,
    _inside,
    _publish_prepare_files,
)
from phase6_review import Phase6ReviewError
from phase6b2_triage import build_triage


def _check_distinct_outputs(output: Path, chapter_dir: Path, review_dir: Path) -> None:
    chapter = _absolute(chapter_dir)
    review = _absolute(review_dir)
    if (
        output == review or _inside(output, review) or _inside(review, output)
        or output == chapter or _inside(output, chapter) or _inside(chapter, output)
    ):
        raise Phase6ReviewError("OUTPUT_INVALID")


def run_triage(
    *,
    inputs,
    chapter_dir: Path,
    review_dir: Path,
    out_dir: Path,
) -> dict:
    """Run one source-backed B1 authentication and publish exactly two new files."""
    output = _absolute(out_dir)
    _check_distinct_outputs(output, chapter_dir, review_dir)
    _check_external_output(output, inputs, must_exist=False)
    authenticated = authenticate_chapter_review_package(
        inputs, chapter_dir=chapter_dir, review_dir=review_dir,
    )
    artifact, handoff = build_triage(
        context=authenticated.context,
        session=authenticated.session,
        evidence_report=authenticated.evidence_report,
        beginner_report=authenticated.beginner_report,
        status=authenticated.status,
        session_sha256=hashlib.sha256(authenticated.package_raw["chapter-review-session.json"]).hexdigest(),
        status_sha256=hashlib.sha256(
            authenticated.package_raw["chapter-review-status.json"]
        ).hexdigest(),
        evidence_report_sha256=hashlib.sha256(
            authenticated.package_raw["evidence-review.json"]
        ).hexdigest(),
        beginner_report_sha256=hashlib.sha256(
            authenticated.package_raw["beginner-review.json"]
        ).hexdigest(),
    )
    triage_raw = dumps_artifact(artifact).encode("utf-8")
    authenticated.recheck()
    _check_distinct_outputs(output, chapter_dir, review_dir)
    _check_external_output(output, inputs, must_exist=False)
    _publish_prepare_files(output, {
        "chapter-repair-triage.json": triage_raw,
        "writer-handoff.md": handoff,
    })
    return artifact


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chapter-dir", required=True)
    parser.add_argument("--review-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    _add_inputs(parser)
    args = parser.parse_args(argv)
    try:
        artifact = run_triage(
            inputs=_paths_from_args(args),
            chapter_dir=Path(args.chapter_dir),
            review_dir=Path(args.review_dir),
            out_dir=Path(args.out_dir),
        )
        print(json.dumps({
            "triage_id": artifact["triage_id"],
            "overall_disposition": artifact["overall_disposition"],
            "source_status": artifact["source_status"],
            "unknown_files": artifact["unknown_files"],
            "finding_counts": artifact["finding_counts"],
        }, sort_keys=True))
        return 0
    except (Phase6ReviewError, Phase6ChapterError) as exc:
        print(exc.code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
