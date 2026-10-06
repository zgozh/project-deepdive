#!/usr/bin/env python3
"""Prepare and finalize an independent Phase 6D1 answer review sidecar."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping

from answer_review import (
    Phase6AnswerReviewError,
    aggregate_status,
    build_review_session_and_packets,
    build_review_templates,
    validate_beginner_report,
    validate_evidence_report,
)
from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact
from chapter_review_workflow import authenticate_chapter_review_package
from phase6_answer_book import Phase6AnswerBookError, build_answer_bank, render_answer_book
from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    _absolute,
    _check_external_output,
    _inside,
    _is_link,
    _publish_new_file,
    _publish_prepare_files,
    _read_review_file,
)
from phase6_review import Phase6ReviewError


_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_PREPARE_FILES = {
    "answer-review-session.json",
    "answer-evidence-verifier-packet.md",
    "answer-beginner-reviewer-packet.md",
    "answer-evidence-review-template.json",
    "answer-beginner-review-template.json",
}
_REVIEW_FILES = _PREPARE_FILES | {
    "answer-evidence-review.json",
    "answer-beginner-review.json",
}
_PACKAGE_FILES = _REVIEW_FILES | {"answer-review-status.json"}
_ANSWER_FILES = {"exercise-bank.json", "ANSWER-BOOK.md"}


class AnswerReviewWorkflowError(ValueError):
    """A fixed, redacted Phase 6D1b workflow failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise AnswerReviewWorkflowError(code)


def _sha256(raw: bytes) -> str:
    import hashlib

    return hashlib.sha256(raw).hexdigest()


def _inside_or_same(path: Path, other: Path) -> bool:
    return path == other or _inside(path, other) or _inside(other, path)


def _ensure_disjoint(output: Path, *inputs: Path) -> None:
    if any(_inside_or_same(output, _absolute(item)) for item in inputs):
        _fail("OUTPUT_INVALID")


def _check_review_output(
    output: Path,
    inputs: ChapterInputPaths,
    *,
    must_exist: bool,
    chapter_dir: Path,
    chapter_review_dir: Path,
    candidate_path: Path,
    answer_dir: Path,
) -> Path:
    try:
        resolved = _check_external_output(output, inputs, must_exist=must_exist)
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
    _ensure_disjoint(resolved, chapter_dir, chapter_review_dir, candidate_path, answer_dir)
    return resolved


def _read_candidate(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_review_file(path)
        candidate = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        validate_artifact(candidate)
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
    except (ArtifactValidationError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AnswerReviewWorkflowError("CANDIDATE_INVALID") from exc
    if candidate.get("artifact_kind") != "answer-candidates":
        _fail("CANDIDATE_INVALID")
    return candidate, raw


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _read_answer_directory(answer_dir: Path) -> dict[str, bytes]:
    directory = _absolute(answer_dir)
    try:
        if _is_link(directory) or not directory.is_dir():
            _fail("ANSWER_PACKAGE_INVALID")
        entries = list(directory.iterdir())
    except OSError as exc:
        raise AnswerReviewWorkflowError("ANSWER_PACKAGE_INVALID") from exc
    if {entry.name for entry in entries} != _ANSWER_FILES or len(entries) != len(_ANSWER_FILES):
        _fail("ANSWER_PACKAGE_INVALID")
    raw: dict[str, bytes] = {}
    try:
        for name in sorted(_ANSWER_FILES):
            raw[name] = _read_review_file(directory / name)
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
    return raw


def _authenticate_answer_book(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    chapter_review_dir: Path,
    answer_candidates: Path,
    answer_dir: Path,
):
    """Replay B1 once, then reproject the candidate and compare exact D1 bytes."""
    try:
        package = authenticate_chapter_review_package(
            inputs, chapter_dir=chapter_dir, review_dir=chapter_review_dir,
        )
    except (Phase6ReviewError, Phase6ChapterError) as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        raise AnswerReviewWorkflowError(code) from exc
    candidate, candidate_raw = _read_candidate(answer_candidates)
    answer_raw = _read_answer_directory(answer_dir)
    try:
        bank = build_answer_bank(package, candidate, candidate_raw)
        bank_raw = dumps_artifact(bank).encode("utf-8")
        book_raw = render_answer_book(bank).encode("utf-8")
    except Phase6AnswerBookError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
    except (ArtifactValidationError, OSError, ValueError, TypeError) as exc:
        raise AnswerReviewWorkflowError("ANSWER_BINDING_INVALID") from exc
    if (
        answer_raw["exercise-bank.json"] != bank_raw
        or answer_raw["ANSWER-BOOK.md"] != book_raw
        or bank.get("schema_version") != "1.1.0"
        or bank.get("answer_book_status") != "DRAFT"
        or bank.get("overall_status") != "PARTIAL"
        or bank.get("answer_evidence_review") != "NOT_RUN"
        or bank.get("answer_beginner_review") != "NOT_RUN"
    ):
        _fail("ANSWER_BINDING_INVALID")
    return package, candidate, candidate_raw, bank, bank_raw, book_raw


def _recheck_answer_inputs(
    package: Any,
    candidate_path: Path,
    candidate_raw: bytes,
    answer_dir: Path,
    answer_raw: Mapping[str, bytes],
) -> None:
    try:
        package.recheck()
        if _read_review_file(candidate_path) != candidate_raw:
            _fail("INPUT_CHANGED")
        current_answer = _read_answer_directory(answer_dir)
        if current_answer != dict(answer_raw):
            _fail("INPUT_CHANGED")
    except (Phase6ChapterError, Phase6ReviewError) as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc


def _review_paths(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    chapter_review_dir: Path,
    answer_candidates: Path,
    answer_dir: Path,
    review_dir: Path,
    must_exist: bool,
) -> tuple[Path, Path, Path, Path, Path]:
    chapter = _absolute(chapter_dir)
    chapter_review = _absolute(chapter_review_dir)
    candidate = _absolute(answer_candidates)
    answers = _absolute(answer_dir)
    output = _check_review_output(
        review_dir, inputs, must_exist=must_exist,
        chapter_dir=chapter, chapter_review_dir=chapter_review,
        candidate_path=candidate, answer_dir=answers,
    )
    return chapter, chapter_review, candidate, answers, output


def prepare_answer_review(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    chapter_review_dir: Path,
    answer_candidates: Path,
    answer_dir: Path,
    review_dir: Path,
) -> dict[str, Any]:
    """Authenticate one immutable D1 answer book and publish role packets/templates."""
    chapter, chapter_review, candidate_path, answers, output = _review_paths(
        inputs, chapter_dir=chapter_dir, chapter_review_dir=chapter_review_dir,
        answer_candidates=answer_candidates, answer_dir=answer_dir,
        review_dir=review_dir, must_exist=False,
    )
    package, candidate, candidate_raw, bank, bank_raw, book_raw = _authenticate_answer_book(
        inputs,
        chapter_dir=chapter,
        chapter_review_dir=chapter_review,
        answer_candidates=candidate_path,
        answer_dir=answers,
    )
    session, evidence_packet, beginner_packet, occurrences, scopes = build_review_session_and_packets(
        package, candidate, candidate_raw, bank, bank_raw, book_raw,
    )
    session_raw = dumps_artifact(session).encode("utf-8")
    evidence_template, beginner_template = build_review_templates(
        session, session_raw, evidence_packet, beginner_packet, occurrences, scopes,
    )
    answer_raw = _read_answer_directory(answers)
    _recheck_answer_inputs(package, candidate_path, candidate_raw, answers, answer_raw)
    _check_review_output(
        output, inputs, must_exist=False, chapter_dir=chapter,
        chapter_review_dir=chapter_review, candidate_path=candidate_path, answer_dir=answers,
    )
    try:
        _publish_prepare_files(output, {
            "answer-review-session.json": session_raw,
            "answer-evidence-verifier-packet.md": evidence_packet,
            "answer-beginner-reviewer-packet.md": beginner_packet,
            "answer-evidence-review-template.json": evidence_template,
            "answer-beginner-review-template.json": beginner_template,
        })
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
    return {
        "command": "prepare",
        "review_session_id": session["review_session_id"],
        "evidence_packet_sha256": session["evidence_packet_sha256"],
        "beginner_packet_sha256": session["beginner_packet_sha256"],
    }


def _read_review_artifact(path: Path, kind: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_review_file(path)
        artifact = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        validate_artifact(artifact)
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
    except (ArtifactValidationError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise AnswerReviewWorkflowError("REPORT_INVALID") from exc
    if artifact.get("artifact_kind") != kind:
        _fail("REPORT_INVALID")
    return artifact, raw


def _verify_review_directory(review_dir: Path, *, include_status: bool = False) -> dict[str, bytes]:
    expected = _PACKAGE_FILES if include_status else _REVIEW_FILES
    output = _absolute(review_dir)
    try:
        if _is_link(output) or not output.is_dir():
            _fail("REVIEW_PACKAGE_INVALID")
        entries = list(output.iterdir())
    except OSError as exc:
        raise AnswerReviewWorkflowError("REVIEW_PACKAGE_INVALID") from exc
    if {entry.name for entry in entries} != expected or len(entries) != len(expected):
        _fail("REVIEW_PACKAGE_INVALID")
    try:
        return {name: _read_review_file(output / name) for name in sorted(expected)}
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc


def _rebuild_review_material(package, candidate, candidate_raw, bank, bank_raw, book_raw):
    session, evidence_packet, beginner_packet, occurrences, scopes = build_review_session_and_packets(
        package, candidate, candidate_raw, bank, bank_raw, book_raw,
    )
    session_raw = dumps_artifact(session).encode("utf-8")
    evidence_template, beginner_template = build_review_templates(
        session, session_raw, evidence_packet, beginner_packet, occurrences, scopes,
    )
    return session, session_raw, evidence_packet, beginner_packet, evidence_template, beginner_template, occurrences, scopes


def _validate_review_package(output: Path, raw_by_name: Mapping[str, bytes], package, candidate, candidate_raw, bank, bank_raw, book_raw):
    material = _rebuild_review_material(package, candidate, candidate_raw, bank, bank_raw, book_raw)
    session, session_raw, evidence_packet, beginner_packet, evidence_template, beginner_template, occurrences, scopes = material
    expected = {
        "answer-review-session.json": session_raw,
        "answer-evidence-verifier-packet.md": evidence_packet,
        "answer-beginner-reviewer-packet.md": beginner_packet,
        "answer-evidence-review-template.json": evidence_template,
        "answer-beginner-review-template.json": beginner_template,
    }
    if any(raw_by_name[name] != value for name, value in expected.items()):
        _fail("SESSION_BINDING_INVALID")
    evidence_report, evidence_raw = _read_review_artifact(
        output / "answer-evidence-review.json", "answer-evidence-review",
    )
    beginner_report, beginner_raw = _read_review_artifact(
        output / "answer-beginner-review.json", "answer-beginner-review",
    )
    if (
        evidence_raw != raw_by_name["answer-evidence-review.json"]
        or beginner_raw != raw_by_name["answer-beginner-review.json"]
    ):
        _fail("INPUT_CHANGED")
    session_sha = _sha256(session_raw)
    validate_evidence_report(
        evidence_report, session, session_sha, session["evidence_packet_sha256"], occurrences,
    )
    validate_beginner_report(
        beginner_report, session, session_sha, session["beginner_packet_sha256"], scopes,
    )
    status = aggregate_status(
        package, session, session_sha, evidence_report, evidence_raw, beginner_report, beginner_raw,
    )
    status_raw = dumps_artifact(status).encode("utf-8")
    if "answer-review-status.json" in raw_by_name:
        stored_status, stored_raw = _read_review_artifact(
            output / "answer-review-status.json", "answer-review-status",
        )
        if stored_raw != raw_by_name["answer-review-status.json"] or stored_raw != status_raw or stored_status != status:
            _fail("REVIEW_STATUS_INVALID")
    return session, evidence_report, beginner_report, status_raw


def finalize_answer_review(
    inputs: ChapterInputPaths,
    *,
    chapter_dir: Path,
    chapter_review_dir: Path,
    answer_candidates: Path,
    answer_dir: Path,
    review_dir: Path,
    evidence_report_path: Path,
    beginner_report_path: Path,
    out_status: Path,
) -> dict[str, Any]:
    """Reauthenticate D1 and publish only one non-promoting answer-review status."""
    chapter, chapter_review, candidate_path, answers, output = _review_paths(
        inputs, chapter_dir=chapter_dir, chapter_review_dir=chapter_review_dir,
        answer_candidates=answer_candidates, answer_dir=answer_dir,
        review_dir=review_dir, must_exist=True,
    )
    status_path = _absolute(out_status)
    if (
        status_path != output / "answer-review-status.json"
        or _absolute(evidence_report_path) != output / "answer-evidence-review.json"
        or _absolute(beginner_report_path) != output / "answer-beginner-review.json"
    ):
        _fail("OUTPUT_INVALID")
    try:
        if os.path.lexists(status_path):
            _fail("OUTPUT_EXISTS")
    except OSError as exc:
        raise AnswerReviewWorkflowError("OUTPUT_INVALID") from exc
    raw_by_name = _verify_review_directory(output)
    package, candidate, candidate_raw, bank, bank_raw, book_raw = _authenticate_answer_book(
        inputs,
        chapter_dir=chapter,
        chapter_review_dir=chapter_review,
        answer_candidates=candidate_path,
        answer_dir=answers,
    )
    _session, _evidence_report, _beginner_report, status_raw = _validate_review_package(
        output, raw_by_name, package, candidate, candidate_raw, bank, bank_raw, book_raw,
    )
    answer_raw = _read_answer_directory(answers)
    _recheck_answer_inputs(package, candidate_path, candidate_raw, answers, answer_raw)
    try:
        if os.path.lexists(status_path):
            _fail("OUTPUT_EXISTS")
        if _verify_review_directory(output) != raw_by_name:
            _fail("INPUT_CHANGED")
    except OSError as exc:
        raise AnswerReviewWorkflowError("INPUT_CHANGED") from exc
    try:
        _publish_new_file(status_path, status_raw)
    except Phase6ChapterError as exc:
        raise AnswerReviewWorkflowError(exc.code) from exc
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
    prepare.add_argument("--chapter-review-dir", required=True)
    prepare.add_argument("--answer-candidates", required=True)
    prepare.add_argument("--answer-dir", required=True)
    prepare.add_argument("--review-dir", required=True)
    _add_inputs(prepare)
    finalize = subparsers.add_parser("finalize")
    finalize.add_argument("--chapter-dir", required=True)
    finalize.add_argument("--chapter-review-dir", required=True)
    finalize.add_argument("--answer-candidates", required=True)
    finalize.add_argument("--answer-dir", required=True)
    finalize.add_argument("--review-dir", required=True)
    finalize.add_argument("--evidence-report", required=True)
    finalize.add_argument("--beginner-report", required=True)
    finalize.add_argument("--out-status", required=True)
    _add_inputs(finalize)
    args = parser.parse_args(argv)
    try:
        inputs = _paths_from_args(args)
        if args.command == "prepare":
            result = prepare_answer_review(
                inputs,
                chapter_dir=Path(args.chapter_dir),
                chapter_review_dir=Path(args.chapter_review_dir),
                answer_candidates=Path(args.answer_candidates),
                answer_dir=Path(args.answer_dir),
                review_dir=Path(args.review_dir),
            )
        else:
            result = finalize_answer_review(
                inputs,
                chapter_dir=Path(args.chapter_dir),
                chapter_review_dir=Path(args.chapter_review_dir),
                answer_candidates=Path(args.answer_candidates),
                answer_dir=Path(args.answer_dir),
                review_dir=Path(args.review_dir),
                evidence_report_path=Path(args.evidence_report),
                beginner_report_path=Path(args.beginner_report),
                out_status=Path(args.out_status),
            )
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (AnswerReviewWorkflowError, Phase6AnswerReviewError, Phase6AnswerBookError) as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(json.dumps({"error": code}, separators=(",", ":")))
        return 2
    except (ArtifactValidationError, Phase6ChapterError, Phase6ReviewError, OSError, ValueError, TypeError):
        print('{"error":"INPUT_INVALID"}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
