#!/usr/bin/env python3
"""Explicit Phase 6B1 prepare/finalize handoff; never invokes a model or provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from artifact_contract import ArtifactValidationError, dumps_artifact
from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    _absolute,
    _check_external_output,
    _inside,
    _publish_new_file,
    _publish_prepare_files,
    _read_artifact,
    _read_review_file,
    authenticate_chapter_for_review,
)
from phase6_review import (
    Phase6ReviewError,
    aggregate_status,
    bind_report,
    build_review_templates,
    build_session_and_packets,
    validate_beginner_report,
    validate_evidence_report,
)


_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_REVIEW_FILES = {
    "chapter-review-session.json",
    "evidence-verifier-packet.md",
    "beginner-critic-packet.md",
    "evidence-review-template.json",
    "beginner-review-template.json",
    "evidence-review.json",
    "beginner-review.json",
}
_REVIEW_PACKAGE_FILES = _REVIEW_FILES | {"chapter-review-status.json"}


@dataclass(frozen=True)
class AuthenticatedChapterReviewPackage:
    """Non-publishing, source-replayed view of one complete immutable B1 package."""

    context: Any
    session: dict[str, Any]
    evidence_report: dict[str, Any]
    beginner_report: dict[str, Any]
    status: dict[str, Any]
    review_dir: Path
    package_raw: dict[str, bytes]

    def recheck(self) -> None:
        """Recheck source and exact package bytes without another semantic replay."""
        try:
            self.context.recheck()
            entries = list(self.review_dir.iterdir())
            if {path.name for path in entries} != _REVIEW_PACKAGE_FILES or len(entries) != len(_REVIEW_PACKAGE_FILES):
                raise Phase6ReviewError("INPUT_CHANGED")
            for name, expected in self.package_raw.items():
                if _read_review_file(self.review_dir / name) != expected:
                    raise Phase6ReviewError("INPUT_CHANGED")
        except Phase6ChapterError as exc:
            raise Phase6ReviewError(exc.code) from exc
        except OSError as exc:
            raise Phase6ReviewError("INPUT_CHANGED") from exc


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _chapter_paths(chapter_dir: Path) -> dict[str, Path]:
    directory = _absolute(chapter_dir)
    return {
        "facts": directory / "chapter-facts.json",
        "packet": directory / "writer-packet.md",
        "chapter": directory / "chapter.md",
        "bank": directory / "exercise-bank.json",
        "status": directory / "chapter-draft-status.json",
    }


def _ensure_separate(output: Path, chapter_dir: Path) -> None:
    chapter = _absolute(chapter_dir)
    if output == chapter or _inside(output, chapter) or _inside(chapter, output):
        raise Phase6ReviewError("OUTPUT_INVALID")


def _context(inputs: ChapterInputPaths, chapter_dir: Path):
    paths = _chapter_paths(chapter_dir)
    try:
        return authenticate_chapter_for_review(
            inputs,
            facts_path=paths["facts"],
            markdown_path=paths["chapter"],
            exercise_bank_path=paths["bank"],
            draft_status_path=paths["status"],
        )
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc


def prepare_chapter_review(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    review_dir: Path,
    writer_alias: str,
) -> dict[str, Any]:
    """Authenticate one immutable 6A chapter and publish role packets/templates."""
    if _ALIAS.fullmatch(writer_alias) is None:
        raise Phase6ReviewError("ROLE_IDENTITY_INVALID")
    output = _absolute(review_dir)
    try:
        _ensure_separate(output, Path(chapter_dir))
        _check_external_output(output, inputs, must_exist=False)
        context = _context(inputs, chapter_dir)
        session, evidence_packet, beginner_packet = build_session_and_packets(context, writer_alias)
        session_raw = dumps_artifact(session).encode("utf-8")
        evidence_template, beginner_template = build_review_templates(
            context, session, session_raw, evidence_packet, beginner_packet,
        )
        context.recheck()
        _check_external_output(output, inputs, must_exist=False)
        _publish_prepare_files(output, {
            "chapter-review-session.json": session_raw,
            "evidence-verifier-packet.md": evidence_packet,
            "beginner-critic-packet.md": beginner_packet,
            "evidence-review-template.json": evidence_template,
            "beginner-review-template.json": beginner_template,
        })
        return session
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc


def _read_review_artifact(path: Path, kind: str) -> tuple[dict[str, Any], bytes]:
    try:
        read = _read_artifact(path)
    except (Phase6ChapterError, ArtifactValidationError) as exc:
        code = exc.code if isinstance(exc, Phase6ChapterError) else "REPORT_INVALID"
        raise Phase6ReviewError(code) from exc
    if read.artifact.get("artifact_kind") != kind:
        raise Phase6ReviewError("REPORT_INVALID")
    if kind == "chapter-review-session" and read.raw != dumps_artifact(read.artifact).encode("utf-8"):
        raise Phase6ReviewError("REPORT_INVALID")
    return dict(read.artifact), read.raw


def _verify_review_directory(
    review_dir: Path,
    inputs: ChapterInputPaths,
    *,
    include_status: bool = False,
) -> tuple[Path, dict[str, bytes]]:
    output = _absolute(review_dir)
    try:
        _check_external_output(output, inputs, must_exist=True)
        entries = list(output.iterdir())
    except (Phase6ChapterError, OSError) as exc:
        code = exc.code if isinstance(exc, Phase6ChapterError) else "INPUT_INVALID"
        raise Phase6ReviewError(code) from exc
    names = {path.name for path in entries}
    expected_names = _REVIEW_PACKAGE_FILES if include_status else _REVIEW_FILES
    if names != expected_names or len(entries) != len(expected_names):
        raise Phase6ReviewError("REVIEW_PACKAGE_INVALID")
    raw_by_name: dict[str, bytes] = {}
    for name in sorted(expected_names):
        try:
            raw_by_name[name] = _read_review_file(output / name)
        except Phase6ChapterError as exc:
            raise Phase6ReviewError(exc.code) from exc
    return output, raw_by_name


def _validate_review_package_files(
    output: Path,
    raw_by_name: dict[str, bytes],
    context: Any,
    *,
    require_status: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes]:
    """Run the shared B1 packet/report/status validation for finalize and replay API."""
    session_path = output / "chapter-review-session.json"
    session, session_raw = _read_review_artifact(session_path, "chapter-review-session")
    if session_raw != raw_by_name["chapter-review-session.json"]:
        raise Phase6ReviewError("INPUT_CHANGED")
    writer_alias = session.get("writer_alias")
    if not isinstance(writer_alias, str) or _ALIAS.fullmatch(writer_alias) is None:
        raise Phase6ReviewError("SESSION_INVALID")
    expected_session, evidence_packet, beginner_packet = build_session_and_packets(context, writer_alias)
    expected_session_raw = dumps_artifact(expected_session).encode("utf-8")
    evidence_template, beginner_template = build_review_templates(
        context, expected_session, expected_session_raw, evidence_packet, beginner_packet,
    )
    if (
        session_raw != expected_session_raw
        or raw_by_name["evidence-verifier-packet.md"] != evidence_packet
        or raw_by_name["beginner-critic-packet.md"] != beginner_packet
        or raw_by_name["evidence-review-template.json"] != evidence_template
        or raw_by_name["beginner-review-template.json"] != beginner_template
    ):
        raise Phase6ReviewError("SESSION_BINDING_INVALID")

    session_sha = _sha256(session_raw)
    evidence_report, evidence_raw = _read_review_artifact(output / "evidence-review.json", "chapter-evidence-review")
    beginner_report, beginner_raw = _read_review_artifact(output / "beginner-review.json", "chapter-beginner-review")
    if (
        evidence_raw != raw_by_name["evidence-review.json"]
        or beginner_raw != raw_by_name["beginner-review.json"]
    ):
        raise Phase6ReviewError("INPUT_CHANGED")
    bind_report(evidence_report, expected_session, session_sha)
    bind_report(beginner_report, expected_session, session_sha)
    validate_evidence_report(evidence_report, context, expected_session["evidence_packet_sha256"])
    validate_beginner_report(beginner_report, context, expected_session["beginner_packet_sha256"])
    status = aggregate_status(
        context,
        expected_session,
        session_sha,
        evidence_report,
        _sha256(evidence_raw),
        beginner_report,
        _sha256(beginner_raw),
    )
    status_raw = dumps_artifact(status).encode("utf-8")
    if require_status:
        stored_status, stored_status_raw = _read_review_artifact(
            output / "chapter-review-status.json", "chapter-review-status",
        )
        if stored_status_raw != raw_by_name["chapter-review-status.json"] or stored_status_raw != status_raw or stored_status != status:
            raise Phase6ReviewError("REVIEW_STATUS_INVALID")
    return expected_session, evidence_report, beginner_report, status_raw


def _recheck_review_files(output: Path, expected: dict[str, bytes]) -> None:
    try:
        entries = {path.name for path in output.iterdir()}
        if entries != set(expected):
            raise Phase6ReviewError("INPUT_CHANGED")
        for name, raw in expected.items():
            if _read_review_file(output / name) != raw:
                raise Phase6ReviewError("INPUT_CHANGED")
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc
    except OSError as exc:
        raise Phase6ReviewError("INPUT_CHANGED") from exc


def authenticate_chapter_review_package(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    review_dir: Path,
) -> AuthenticatedChapterReviewPackage:
    """Replay source once and authenticate all eight immutable Phase 6B1 files without writing."""
    output = _absolute(review_dir)
    try:
        _ensure_separate(output, Path(chapter_dir))
        _check_external_output(output, inputs, must_exist=True)
        output, raw_by_name = _verify_review_directory(output, inputs, include_status=True)
        context = _context(inputs, chapter_dir)
    except Phase6ReviewError:
        raise
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc

    session, evidence_report, beginner_report, status_raw = _validate_review_package_files(
        output, raw_by_name, context, require_status=True,
    )
    expected_raw = {name: raw_by_name[name] for name in _REVIEW_PACKAGE_FILES}
    try:
        context.recheck()
        _recheck_review_files(output, expected_raw)
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc
    status = json.loads(status_raw.decode("utf-8"))
    return AuthenticatedChapterReviewPackage(
        context=context,
        session=session,
        evidence_report=evidence_report,
        beginner_report=beginner_report,
        status=status,
        review_dir=output,
        package_raw=expected_raw,
    )


def finalize_chapter_review(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    review_dir: Path,
    evidence_report_path: Path,
    beginner_report_path: Path,
    out_status: Path,
) -> dict[str, Any]:
    """Reauthenticate the same source/chapter and publish one non-promoting status."""
    output = _absolute(review_dir)
    status_path = _absolute(out_status)
    expected_status = output / "chapter-review-status.json"
    expected_evidence = output / "evidence-review.json"
    expected_beginner = output / "beginner-review.json"
    if (
        status_path != expected_status
        or _absolute(evidence_report_path) != expected_evidence
        or _absolute(beginner_report_path) != expected_beginner
    ):
        raise Phase6ReviewError("OUTPUT_INVALID")
    try:
        _ensure_separate(output, Path(chapter_dir))
        _check_external_output(output, inputs, must_exist=True)
        if os.path.lexists(status_path):
            raise Phase6ReviewError("OUTPUT_EXISTS")
        output, raw_by_name = _verify_review_directory(output, inputs)
        context = _context(inputs, chapter_dir)
    except Phase6ReviewError:
        raise
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc

    session, evidence_report, beginner_report, status_raw = _validate_review_package_files(
        output, raw_by_name, context, require_status=False,
    )

    # The single source replay above is followed by freshness and byte rechecks, not another replay.
    context.recheck()
    try:
        if os.path.lexists(status_path):
            raise Phase6ReviewError("OUTPUT_EXISTS")
        _recheck_review_files(output, raw_by_name)
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc
    except OSError as exc:
        raise Phase6ReviewError("INPUT_CHANGED") from exc
    try:
        _publish_new_file(status_path, status_raw)
    except Phase6ChapterError as exc:
        raise Phase6ReviewError(exc.code) from exc
    return json.loads(status_raw.decode("utf-8"))


def _paths_from_args(args: argparse.Namespace) -> ChapterInputPaths:
    return ChapterInputPaths(
        phase4c_package=Path(args.phase4c_package),
        claim_candidates_path=Path(args.claim_candidates),
        claim_evidence_path=Path(args.claim_evidence),
        claim_evidence_graph_path=Path(args.claim_evidence_graph),
        prerequisite_candidates_path=Path(args.prerequisite_candidates),
        prerequisite_graph_path=Path(args.prerequisite_graph),
        curriculum_candidates_path=Path(args.curriculum_candidates),
        curriculum_path=Path(args.curriculum),
        run_dir=Path(args.run_dir),
        root=Path(args.root),
    )


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--phase4c-package", required=True)
    parser.add_argument("--claim-candidates", required=True)
    parser.add_argument("--claim-evidence", required=True)
    parser.add_argument("--claim-evidence-graph", required=True)
    parser.add_argument("--prerequisite-candidates", required=True)
    parser.add_argument("--prerequisite-graph", required=True)
    parser.add_argument("--curriculum-candidates", required=True)
    parser.add_argument("--curriculum", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--chapter-dir", required=True)
    prepare.add_argument("--review-dir", required=True)
    prepare.add_argument("--writer-alias", required=True)
    _add_inputs(prepare)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--chapter-dir", required=True)
    finalize.add_argument("--review-dir", required=True)
    finalize.add_argument("--evidence-report", required=True)
    finalize.add_argument("--beginner-report", required=True)
    finalize.add_argument("--out-status", required=True)
    _add_inputs(finalize)
    args = parser.parse_args(argv)
    try:
        inputs = _paths_from_args(args)
        if args.command == "prepare":
            result = prepare_chapter_review(
                inputs,
                chapter_dir=Path(args.chapter_dir),
                review_dir=Path(args.review_dir),
                writer_alias=args.writer_alias,
            )
        else:
            result = finalize_chapter_review(
                inputs,
                chapter_dir=Path(args.chapter_dir),
                review_dir=Path(args.review_dir),
                evidence_report_path=Path(args.evidence_report),
                beginner_report_path=Path(args.beginner_report),
                out_status=Path(args.out_status),
            )
        print(dumps_artifact(result), end="")
        return 0
    except (Phase6ReviewError, Phase6ChapterError) as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(f'{{"error":"{code}"}}')
        return 2
    except (ArtifactValidationError, OSError, ValueError, TypeError):
        print('{"error":"INPUT_INVALID"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
