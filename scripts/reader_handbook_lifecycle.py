#!/usr/bin/env python3
"""Bind, review, and publish one exact reader chapter without claiming semantic proof."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Mapping

import curriculum_run_workflow
import phase6_chapter as chapter
import reader_handbook_review as reader_review
import reader_handbook_workflow as reader_workflow


class ReaderHandbookLifecycleError(ValueError):
    """A bounded, redacted lifecycle failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise ReaderHandbookLifecycleError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ReaderHandbookLifecycleError("REVIEW_INPUT_INVALID") from exc


def _strict_json(raw: bytes, code: str) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=unique_object,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid JSON constant")),
        )
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ReaderHandbookLifecycleError(code) from exc
    if not isinstance(value, dict):
        _fail(code)
    return value


def _nonempty(value: Any, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(code)
    return value.strip()


def _bound_bytes(context: reader_review.AuthenticatedReaderReviewContext, name: str) -> bytes:
    for file_name, _path, raw in context._bound_files:
        if file_name == name:
            return raw
    _fail("WRITER_INPUT_INVALID")


def _require_bound_inputs(
    review_context: reader_review.AuthenticatedReaderReviewContext,
    supplied: chapter.ChapterInputPaths,
) -> chapter.ChapterInputPaths:
    try:
        bound = review_context._auth_context.inputs
    except (AttributeError, TypeError, ValueError) as exc:
        raise ReaderHandbookLifecycleError("INPUT_CONTEXT_INVALID") from exc
    if not isinstance(bound, chapter.ChapterInputPaths):
        _fail("INPUT_CONTEXT_INVALID")
    if not isinstance(supplied, chapter.ChapterInputPaths) or supplied != bound:
        _fail("INPUT_CONTEXT_MISMATCH")
    return bound


def _reject_reader_internal_output(
    path: Path,
    context: ReaderChapterReviewContext,
) -> None:
    skill_root = reader_workflow._SKILL_ROOT.resolve(strict=True)
    protected = [skill_root, context.source_context.attempt_dir]
    if context.staging_dir is not None:
        protected.append(context.staging_dir)
    for directory in protected:
        if chapter._inside(path, directory) or chapter._inside(directory, path):
            _fail("OUTPUT_INVALID")


def _validate_markdown(raw: bytes, *, kind: str) -> str:
    if not raw:
        _fail("CHAPTER_EMPTY")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ReaderHandbookLifecycleError("CHAPTER_INVALID_UTF8") from exc
    if not text.strip():
        _fail("CHAPTER_EMPTY")

    heading_one = 0
    heading_two = 0
    fence_char: str | None = None
    fence_size = 0
    for line in text.splitlines():
        if fence_char is not None:
            closing = re.fullmatch(r" {0,3}(`+|~+)[ \t]*", line)
            if (
                closing is not None
                and closing.group(1)[0] == fence_char
                and len(closing.group(1)) >= fence_size
            ):
                fence_char = None
                fence_size = 0
            continue

        opening = re.match(r" {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening is not None:
            marker, info = opening.groups()
            if marker[0] == "`" and "`" in info:
                continue
            fence_char = marker[0]
            fence_size = len(marker)
            continue

        if "@@source:" in line:
            _fail("SOURCE_SLOT_UNRESOLVED")
        heading = re.match(r" {0,3}(#{1,6})[ \t]+\S", line)
        if heading is not None:
            level = len(heading.group(1))
            heading_one += level == 1
            heading_two += level == 2
    if fence_char is not None:
        _fail("CODE_FENCE_UNCLOSED")
    if kind == "lesson" and heading_one != 1:
        _fail("CHAPTER_H1_INVALID")
    if kind == "answers":
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if first.startswith("# ") or not re.match(r"^##[ \t]+\S", first):
            _fail("ANSWERS_HEADING_INVALID")
        if heading_one != 0 or heading_two == 0:
            _fail("ANSWERS_HEADING_INVALID")
    return text


def _source_review_bytes(context: reader_review.AuthenticatedReaderReviewContext) -> bytes:
    try:
        raw = context._review_input_json.encode("utf-8", errors="strict")
        review_input = context.review_input
        if _canonical_json(review_input) != raw:
            _fail("REVIEW_INPUT_INVALID")
        return raw
    except (AttributeError, UnicodeError, TypeError, ValueError) as exc:
        raise ReaderHandbookLifecycleError("REVIEW_INPUT_INVALID") from exc


@dataclass(frozen=True)
class ReaderChapterReviewContext:
    """One exact composition and source-review packet captured for human review."""

    source_context: reader_review.AuthenticatedReaderReviewContext
    answer_mode: str
    final_chapter_bytes: bytes
    review_input_bytes: bytes
    final_chapter_sha256: str
    review_input_sha256: str
    staging_dir: Path | None = None

    @property
    def review_input(self) -> dict[str, Any]:
        return _strict_json(self.review_input_bytes, "REVIEW_INPUT_INVALID")


def prepare_reader_chapter_review(
    review_context: reader_review.AuthenticatedReaderReviewContext,
    *,
    answer_mode: str = "append",
) -> ReaderChapterReviewContext:
    """Compose the final bytes without rewriting either authored manuscript."""
    if not isinstance(review_context, reader_review.AuthenticatedReaderReviewContext):
        _fail("INPUT_CONTEXT_INVALID")
    if answer_mode not in {"append", "embedded"}:
        _fail("ANSWER_MODE_INVALID")
    try:
        review_context.recheck()
    except (reader_review.ReaderHandbookReviewError, chapter.Phase6ChapterError) as exc:
        raise ReaderHandbookLifecycleError(getattr(exc, "code", "INPUT_CHANGED")) from exc

    lesson_raw = _bound_bytes(review_context, "lesson.md")
    answers_raw = _bound_bytes(review_context, "answers.md")
    _validate_markdown(lesson_raw, kind="lesson")
    _validate_markdown(answers_raw, kind="answers")
    if len(answers_raw.strip()) == 0:
        _fail("ANSWERS_EMPTY")

    if answer_mode == "append":
        if lesson_raw.count(answers_raw) != 0:
            _fail("ANSWERS_DUPLICATE")
        final_raw = lesson_raw + b"\n\n" + answers_raw
    else:
        count = lesson_raw.count(answers_raw)
        if count == 0:
            _fail("ANSWERS_NOT_EMBEDDED")
        if count != 1:
            _fail("ANSWERS_DUPLICATE")
        final_raw = lesson_raw

    _validate_markdown(final_raw, kind="lesson")
    source_input_raw = _source_review_bytes(review_context)
    source_input = _strict_json(source_input_raw, "REVIEW_INPUT_INVALID")
    source_proofs = source_input.get("source_proofs")
    if not isinstance(source_proofs, list) or not source_proofs:
        _fail("SOURCE_PROOF_REQUIRED")

    packet_body: dict[str, Any] = {
        "artifact_kind": "reader-handbook-chapter-review-input",
        "version": "1.0",
        "status": "REVIEW_INPUT_READY",
        "answer_mode": answer_mode,
        "final_chapter_sha256": _sha256(final_raw),
        "final_chapter_bytes_base64": base64.b64encode(final_raw).decode("ascii"),
        "final_chapter": final_raw.decode("utf-8", errors="strict"),
        "source_review_input_sha256": _sha256(source_input_raw),
        "source_review_input": source_input,
    }
    fingerprint = _sha256(_canonical_json(packet_body))
    packet = {**packet_body, "fingerprint_sha256": fingerprint}
    packet_raw = _canonical_json(packet)
    return ReaderChapterReviewContext(
        source_context=review_context,
        answer_mode=answer_mode,
        final_chapter_bytes=final_raw,
        review_input_bytes=packet_raw,
        final_chapter_sha256=_sha256(final_raw),
        review_input_sha256=_sha256(packet_raw),
    )


def write_reader_chapter_review_input(
    context: ReaderChapterReviewContext,
    *,
    inputs: chapter.ChapterInputPaths,
    out_dir: str | Path,
) -> ReaderChapterReviewContext:
    """Write a new internal staging folder for the exact bytes reviewers will inspect."""
    if not isinstance(context, ReaderChapterReviewContext):
        _fail("INPUT_CONTEXT_INVALID")
    bound_inputs = _require_bound_inputs(context.source_context, inputs)
    _recheck_context(context)
    try:
        output = chapter._check_external_output(out_dir, bound_inputs, must_exist=False)
        _reject_reader_internal_output(output, context)
        chapter._publish_prepare_files(output, {
            "final-chapter.md": context.final_chapter_bytes,
            "review-input.json": context.review_input_bytes,
        })
        return replace(context, staging_dir=output.resolve(strict=True))
    except ReaderHandbookLifecycleError:
        raise
    except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookLifecycleError(getattr(exc, "code", "OUTPUT_INVALID")) from exc


def _recheck_context(context: ReaderChapterReviewContext) -> None:
    try:
        context.source_context.recheck()
    except (reader_review.ReaderHandbookReviewError, chapter.Phase6ChapterError) as exc:
        raise ReaderHandbookLifecycleError(getattr(exc, "code", "INPUT_CHANGED")) from exc
    if (
        _sha256(context.final_chapter_bytes) != context.final_chapter_sha256
        or _sha256(context.review_input_bytes) != context.review_input_sha256
    ):
        _fail("REVIEW_BINDING_CHANGED")
    packet = _strict_json(context.review_input_bytes, "REVIEW_INPUT_INVALID")
    if (
        packet.get("final_chapter_sha256") != context.final_chapter_sha256
        or packet.get("final_chapter_bytes_base64") != base64.b64encode(context.final_chapter_bytes).decode("ascii")
        or packet.get("source_review_input_sha256") != _sha256(_source_review_bytes(context.source_context))
    ):
        _fail("REVIEW_BINDING_CHANGED")
    if context.staging_dir is not None:
        try:
            chapter._assert_no_link_components(context.staging_dir)
            if context.staging_dir.is_symlink() or not context.staging_dir.is_dir():
                _fail("REVIEW_BINDING_CHANGED")
            if chapter._read_review_file(context.staging_dir / "final-chapter.md") != context.final_chapter_bytes:
                _fail("REVIEW_BINDING_CHANGED")
            if chapter._read_review_file(context.staging_dir / "review-input.json") != context.review_input_bytes:
                _fail("REVIEW_BINDING_CHANGED")
        except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise ReaderHandbookLifecycleError("REVIEW_BINDING_CHANGED") from exc


def _read_report(path_value: str | Path, *, code: str) -> tuple[Path, bytes, dict[str, Any]]:
    try:
        path = chapter._absolute(path_value)
        raw = chapter._read_review_file(path)
    except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookLifecycleError(code) from exc
    return path, raw, _strict_json(raw, code)


_COMMON_REPORT_FIELDS = {
    "kind", "version", "verdict", "final_chapter_sha256", "review_input_sha256",
    "reviewer_session_id", "complete_manuscript_read", "assessment", "blocking_findings",
}
_SOURCE_REPORT_FIELDS = _COMMON_REPORT_FIELDS | {"evidence_assessment", "limitation_assessment"}
_TEACHING_REPORT_FIELDS = _COMMON_REPORT_FIELDS | {
    "normal_trace", "failure_trace", "beginner_assessment",
    "source_explanation_assessment", "answer_consistency",
}


def _validate_report(
    report: Mapping[str, Any],
    *,
    kind: str,
    expected_fields: set[str],
    context: ReaderChapterReviewContext,
    writer_session_id: str,
) -> str:
    if set(report) != expected_fields:
        _fail("REVIEW_REPORT_INVALID")
    if report.get("kind") != kind or report.get("version") != "1.0":
        _fail("REVIEW_REPORT_INVALID")
    if report.get("verdict") != "ACCEPTED":
        _fail("REVIEW_NOT_ACCEPTED")
    if (
        report.get("final_chapter_sha256") != context.final_chapter_sha256
        or report.get("review_input_sha256") != context.review_input_sha256
        or report.get("complete_manuscript_read") is not True
    ):
        _fail("REVIEW_BINDING_CHANGED")
    session = _nonempty(report.get("reviewer_session_id"), "REVIEW_REPORT_INVALID")
    if session == writer_session_id or any(ord(char) < 32 for char in session):
        _fail("REVIEWER_NOT_INDEPENDENT")
    for name in expected_fields - _COMMON_REPORT_FIELDS - {"kind", "version", "verdict"}:
        _nonempty(report.get(name), "REVIEW_REPORT_INVALID")
    _nonempty(report.get("assessment"), "REVIEW_REPORT_INVALID")
    findings = report.get("blocking_findings")
    if not isinstance(findings, list):
        _fail("REVIEW_REPORT_INVALID")
    if findings:
        _fail("REVIEW_NOT_ACCEPTED")
    return session


@dataclass(frozen=True)
class AcceptedReaderChapter:
    """A reviewer-attested draft bound to one composition and two exact reports."""

    review_context: ReaderChapterReviewContext
    source_review_path: Path
    source_review_bytes: bytes
    teaching_review_path: Path
    teaching_review_bytes: bytes
    source_reviewer_session_id: str
    teaching_reviewer_session_id: str
    status: str = "READER_REVIEWED_DRAFT"
    coverage: str = "PARTIAL"


def accept_reader_chapter_review(
    context: ReaderChapterReviewContext,
    *,
    source_review_report: str | Path,
    teaching_review_report: str | Path,
) -> AcceptedReaderChapter:
    """Validate independent human attestations; this function does not assess their truth."""
    if not isinstance(context, ReaderChapterReviewContext) or context.staging_dir is None:
        _fail("REVIEW_INPUT_REQUIRED")
    _recheck_context(context)
    review_input = context.review_input
    source_input = review_input.get("source_review_input")
    writer_session = source_input.get("writer_session_id") if isinstance(source_input, dict) else None
    writer_session = _nonempty(writer_session, "REVIEW_INPUT_INVALID")
    unknowns = source_input.get("unknowns", []) if isinstance(source_input, dict) else []
    if any(isinstance(row, dict) and row.get("critical") is True for row in unknowns):
        _fail("CRITICAL_UNKNOWN")

    source_path, source_raw, source_report = _read_report(
        source_review_report, code="SOURCE_REVIEW_INVALID",
    )
    teaching_path, teaching_raw, teaching_report = _read_report(
        teaching_review_report, code="TEACHING_REVIEW_INVALID",
    )
    if source_path == teaching_path:
        _fail("REVIEWER_NOT_INDEPENDENT")
    source_session = _validate_report(
        source_report,
        kind="reader-source-evidence-review",
        expected_fields=_SOURCE_REPORT_FIELDS,
        context=context,
        writer_session_id=writer_session,
    )
    teaching_session = _validate_report(
        teaching_report,
        kind="reader-principal-teaching-review",
        expected_fields=_TEACHING_REPORT_FIELDS,
        context=context,
        writer_session_id=writer_session,
    )
    if source_session == teaching_session:
        _fail("REVIEWER_NOT_INDEPENDENT")
    return AcceptedReaderChapter(
        review_context=context,
        source_review_path=source_path,
        source_review_bytes=source_raw,
        teaching_review_path=teaching_path,
        teaching_review_bytes=teaching_raw,
        source_reviewer_session_id=source_session,
        teaching_reviewer_session_id=teaching_session,
    )


def _recheck_accepted(accepted: AcceptedReaderChapter) -> None:
    _recheck_context(accepted.review_context)
    try:
        if chapter._read_review_file(accepted.source_review_path) != accepted.source_review_bytes:
            _fail("REVIEW_BINDING_CHANGED")
        if chapter._read_review_file(accepted.teaching_review_path) != accepted.teaching_review_bytes:
            _fail("REVIEW_BINDING_CHANGED")
    except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookLifecycleError("REVIEW_BINDING_CHANGED") from exc


def _safe_reader_filename(path: Path, *, suffix: str) -> bool:
    name = path.name
    if not name.endswith(suffix) or name in {".", ".."} or name.endswith((".", " ")):
        return False
    return not any(char in name for char in '<>:"/\\|?*') and all(ord(char) >= 32 for char in name)


def _cleanup_created(path: Path, owner: tuple[int, int], expected_bytes: bytes) -> bool:
    try:
        info = path.lstat()
        if (info.st_dev, info.st_ino) != owner or path.read_bytes() != expected_bytes:
            return False
        chapter._unlink_if_owned(path, owner)
        return not path.exists()
    except OSError:
        return False


def publish_accepted_reader_chapter(
    accepted: AcceptedReaderChapter,
    *,
    inputs: chapter.ChapterInputPaths,
    chapter_out: str | Path,
    receipt_out: str | Path,
) -> dict[str, Any]:
    """Publish only the reviewed bytes as new files, with a matching external receipt."""
    if not isinstance(accepted, AcceptedReaderChapter):
        _fail("REVIEW_NOT_ACCEPTED")
    bound_inputs = _require_bound_inputs(accepted.review_context.source_context, inputs)
    _recheck_accepted(accepted)
    try:
        chapter_path = chapter._check_external_output(chapter_out, bound_inputs, must_exist=False)
        receipt_path = chapter._check_external_output(receipt_out, bound_inputs, must_exist=False)
        _reject_reader_internal_output(chapter_path, accepted.review_context)
        _reject_reader_internal_output(receipt_path, accepted.review_context)
        if not _safe_reader_filename(chapter_path, suffix=".md"):
            _fail("OUTPUT_INVALID")
        if not _safe_reader_filename(receipt_path, suffix=".json"):
            _fail("OUTPUT_INVALID")
        if chapter_path == receipt_path or chapter._inside(receipt_path, chapter_path.parent):
            _fail("OUTPUT_INVALID")
    except ReaderHandbookLifecycleError:
        raise
    except (chapter.Phase6ChapterError, OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ReaderHandbookLifecycleError(getattr(exc, "code", "OUTPUT_INVALID")) from exc

    review_input = accepted.review_context.review_input
    source_anchor = review_input["source_review_input"]["source_anchor"]
    receipt = {
        "artifact_kind": "reader-handbook-publication-receipt",
        "version": "1.0",
        "status": "READER_REVIEWED_DRAFT",
        "coverage": "PARTIAL",
        "published_file_name": chapter_path.name,
        "published_chapter_sha256": accepted.review_context.final_chapter_sha256,
        "input_fingerprint_sha256": review_input["fingerprint_sha256"],
        "review_input_sha256": accepted.review_context.review_input_sha256,
        "source_review_sha256": _sha256(accepted.source_review_bytes),
        "teaching_review_sha256": _sha256(accepted.teaching_review_bytes),
        "source_reviewer_session_id": accepted.source_reviewer_session_id,
        "teaching_reviewer_session_id": accepted.teaching_reviewer_session_id,
        "writer_session_id": review_input["source_review_input"]["writer_session_id"],
        "repository_revision": source_anchor["repository_revision"],
        "snapshot_kind": source_anchor["snapshot_kind"],
        "identity_boundary": (
            "Reviewer session IDs are declared attestations and are not cryptographic identity proof."
        ),
    }
    receipt_raw = json.dumps(
        receipt, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
    ).encode("utf-8") + b"\n"
    _recheck_accepted(accepted)
    chapter_owner: tuple[int, int] | None = None
    try:
        chapter_owner = chapter._publish_new_file(
            chapter_path, accepted.review_context.final_chapter_bytes,
        )
        chapter._publish_new_file(receipt_path, receipt_raw)
    except (chapter.Phase6ChapterError, OSError) as exc:
        if chapter_owner is not None:
            if not _cleanup_created(
                chapter_path, chapter_owner, accepted.review_context.final_chapter_bytes,
            ):
                raise ReaderHandbookLifecycleError("PUBLICATION_PARTIAL") from exc
        raise ReaderHandbookLifecycleError(getattr(exc, "code", "OUTPUT_FAILED")) from exc
    return receipt


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare, review-bind, and publish one exact reader chapter.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "review-input", "publish"):
        sub = subparsers.add_parser(command)
        curriculum_run_workflow._add_inputs(sub)
        sub.add_argument("--plan", required=True, type=Path)
    prepare = subparsers.choices["prepare"]
    prepare.add_argument("--unit", action="append", required=True)
    prepare.add_argument("--blueprint", required=True, type=Path)
    prepare.add_argument("--book-plan", type=Path)
    prepare.add_argument("--writer-session", required=True)
    prepare.add_argument("--out", required=True, type=Path)

    review = subparsers.choices["review-input"]
    review.add_argument("--attempt-dir", required=True, type=Path)
    review.add_argument("--answer-mode", choices=("append", "embedded"), default="append")
    review.add_argument("--out", required=True, type=Path)

    publish = subparsers.choices["publish"]
    publish.add_argument("--attempt-dir", required=True, type=Path)
    publish.add_argument("--answer-mode", choices=("append", "embedded"), default="append")
    publish.add_argument("--review-input-dir", required=True, type=Path)
    publish.add_argument("--source-review", required=True, type=Path)
    publish.add_argument("--teaching-review", required=True, type=Path)
    publish.add_argument("--chapter-out", required=True, type=Path)
    publish.add_argument("--receipt-out", required=True, type=Path)
    return parser


def _authenticate(args: argparse.Namespace):
    inputs = curriculum_run_workflow._inputs(args)
    context = curriculum_run_workflow.authenticate_curriculum_run_plan(inputs, plan_path=args.plan)
    return inputs, context


def _review_context(
    auth_context: curriculum_run_workflow.AuthenticatedCurriculumRunPlan,
    attempt_dir: Path,
    answer_mode: str,
) -> ReaderChapterReviewContext:
    base = reader_review.prepare_reader_review(auth_context, attempt_dir)
    return prepare_reader_chapter_review(base, answer_mode=answer_mode)


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs, auth_context = _authenticate(args)
        if args.command == "prepare":
            manifest = reader_workflow.prepare_reader_theme(
                auth_context,
                args.unit,
                args.blueprint,
                args.out,
                args.writer_session,
                book_plan_path=args.book_plan,
            )
            print(f"status=PREPARED writer_session_id={manifest['writer_session_id']}")
            return 0
        if args.command == "review-input":
            review_context = _review_context(auth_context, args.attempt_dir, args.answer_mode)
            review_context = write_reader_chapter_review_input(
                review_context, inputs=inputs, out_dir=args.out,
            )
            print(
                f"status=REVIEW_INPUT_READY final_chapter_sha256={review_context.final_chapter_sha256} "
                f"review_input_sha256={review_context.review_input_sha256}"
            )
            return 0

        review_context = _review_context(auth_context, args.attempt_dir, args.answer_mode)
        review_context = replace(
            review_context, staging_dir=chapter._absolute(args.review_input_dir).resolve(strict=True),
        )
        accepted = accept_reader_chapter_review(
            review_context,
            source_review_report=args.source_review,
            teaching_review_report=args.teaching_review,
        )
        receipt = publish_accepted_reader_chapter(
            accepted,
            inputs=inputs,
            chapter_out=args.chapter_out,
            receipt_out=args.receipt_out,
        )
        print(
            f"status={receipt['status']} coverage={receipt['coverage']} "
            f"published_chapter_sha256={receipt['published_chapter_sha256']}"
        )
        return 0
    except ReaderHandbookLifecycleError as exc:
        print(f"error={exc.code}")
    except reader_review.ReaderHandbookReviewError as exc:
        print(f"error={exc.code}")
    except reader_workflow.ReaderHandbookWorkflowError as exc:
        print(f"error={exc.code}")
    except curriculum_run_workflow.Phase6CurriculumRunError as exc:
        print(f"error={exc.code}")
    except chapter.Phase6ChapterError as exc:
        print(f"error={exc.code}")
    except (OSError, RuntimeError, TypeError, ValueError):
        print("error=INPUT_INVALID")
    except Exception:
        print("error=INTERNAL")
    return 2


if __name__ == "__main__":
    sys.exit(main())
