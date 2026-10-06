"""Assemble an explicitly selected whole-book draft from authenticated D2B attempts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Mapping

import answer_review_workflow as d1
import curriculum_run_workflow as planning
import curriculum_run_execution_workflow as execution
import phase6_chapter as chapter
from artifact_contract import ArtifactValidationError, _strict_json_loads, dumps_artifact, validate_artifact
from phase6_curriculum_run_state import CurriculumRunStateError, load_state_chain, sha256, validate_state_against_plan
from phase6_review import Phase6ReviewError
from phase6_chapter import Phase6ChapterError
from phase6_whole_book import (
    WholeBookError,
    build_whole_book_manifest,
    render_answer_book,
    render_quality_and_gaps,
    render_whole_book,
    validate_whole_book_selection,
)


class WholeBookWorkflowError(ValueError):
    """A fixed, redacted D3a authentication/publication failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise WholeBookWorkflowError(code)


def _source_binding(value: Mapping[str, Any], plan: Mapping[str, Any], *, require_inputs: bool = True) -> None:
    fields = ["repository_revision", "snapshot_kind", "source_metadata", "source_status",
              "source_run_manifest_sha256", "unknown_files"]
    if require_inputs:
        fields.append("input_digests")
    if any(value.get(key) != plan.get(key) for key in fields):
        _fail("SOURCE_BINDING_INVALID")


def _canonical_artifact(raw: bytes, kind: str) -> dict[str, Any]:
    try:
        value = _strict_json_loads(raw.decode("utf-8"))
        validate_artifact(value)
        if not isinstance(value, dict) or value.get("artifact_kind") != kind or dumps_artifact(value).encode("utf-8") != raw:
            _fail("ARTIFACT_INVALID")
        return value
    except WholeBookWorkflowError:
        raise
    except (ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise WholeBookWorkflowError("ARTIFACT_INVALID") from exc


def _parse_pairs(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        unit_id, separator, path_text = value.partition("=")
        if not separator or not unit_id or not path_text or unit_id in result:
            _fail("SELECTION_INVALID")
        result[unit_id] = Path(path_text)
    return result


def _attempt_digests(state: Mapping[str, Any], state_row: Mapping[str, Any], attempt_id: str) -> list[dict[str, Any]]:
    if state["schema_version"] == "1.0.0":
        if attempt_id != "attempt-0001":
            _fail("ATTEMPT_NOT_AVAILABLE")
        return state_row["artifact_digests"]
    attempts = [item for item in state_row["attempts"] if item["attempt_id"] == attempt_id]
    if len(attempts) != 1:
        _fail("ATTEMPT_NOT_AVAILABLE")
    return attempts[0]["artifact_digests"]


def _recorded_files(
    state_dir: Path, row: Mapping[str, Any], attempt_id: str, records: list[Mapping[str, Any]],
) -> tuple[dict[str, bytes], dict[str, Path]]:
    raw_by_role: dict[str, bytes] = {}
    path_by_role: dict[str, Path] = {}
    expected_prefix = f"units/{row['position']:04d}/{attempt_id}/"
    try:
        state_root = state_dir.resolve()
    except (OSError, RuntimeError, ValueError):
        _fail("ATTEMPT_BINDING_INVALID")
    for record in records:
        relative = record.get("relative_path")
        role = record.get("role")
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or not relative.startswith(expected_prefix)
            or role in raw_by_role
        ):
            _fail("ATTEMPT_BINDING_INVALID")
        pure = PurePosixPath(relative)
        if (
            pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or any(PureWindowsPath(part).drive for part in pure.parts)
            or pure.as_posix() != relative
        ):
            _fail("ATTEMPT_BINDING_INVALID")
        path = state_dir.joinpath(*pure.parts)
        try:
            resolved_path = path.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            _fail("ATTEMPT_BINDING_INVALID")
        try:
            resolved_path.relative_to(state_root)
        except ValueError:
            _fail("ATTEMPT_BINDING_INVALID")
        if resolved_path == state_root:
            _fail("ATTEMPT_BINDING_INVALID")
        try:
            chapter._assert_no_link_components(path)
            raw = chapter._read_review_file(path)
        except Phase6ChapterError as exc:
            raise WholeBookWorkflowError("ATTEMPT_OUTPUT_INVALID") from exc
        if sha256(raw) != record.get("sha256"):
            _fail("ATTEMPT_OUTPUT_DRIFT")
        raw_by_role[role] = raw
        path_by_role[role] = path
    return raw_by_role, path_by_role


def _role(raws: Mapping[str, bytes], role: str) -> bytes:
    raw = raws.get(role)
    if raw is None:
        _fail("ATTEMPT_BINDING_INVALID")
    return raw


def _verify_selected_attempt(
    plan: Mapping[str, Any], state: Mapping[str, Any], state_dir: Path,
    plan_row: Mapping[str, Any], state_row: Mapping[str, Any], attempt_id: str,
) -> tuple[str, str | None, dict[str, Any]]:
    records = _attempt_digests(state, state_row, attempt_id)
    raws, paths = _recorded_files(state_dir, state_row, attempt_id, records)
    identity = plan_row["unit_identity"]
    route = plan_row["route"]
    if route == "PHASE6A_PROJECT_CLAIM":
        status_raw = _role(raws, "chapter_review_status")
        facts_raw = _role(raws, "chapter_facts")
        chapter_raw = _role(raws, "chapter_markdown")
        status = _canonical_artifact(status_raw, "chapter-review-status")
        facts = _canonical_artifact(facts_raw, "chapter-facts")
        _source_binding(status, plan, require_inputs=False)
        _source_binding(facts, plan)
        selected_unit = facts.get("selected_unit", {})
        if selected_unit.get("curriculum_unit_id") != identity["id"]:
            _fail("UNIT_BINDING_INVALID")
        if (
            status.get("review_state") != "REVIEWED_DRAFT"
            or status.get("chapter_id") != selected_unit.get("chapter_id")
            or status.get("review_session_sha256") != sha256(_role(raws, "chapter_review_session"))
            or status.get("chapter_facts_sha256") != sha256(facts_raw)
            or status.get("chapter_sha256") != sha256(chapter_raw)
            or status.get("exercise_bank_sha256") != sha256(_role(raws, "exercise_bank"))
            or status.get("evidence_report_sha256") != sha256(_role(raws, "evidence_report"))
            or status.get("beginner_report_sha256") != sha256(_role(raws, "beginner_report"))
        ):
            _fail("REVIEW_STATUS_BINDING_INVALID")
        body = chapter_raw.decode("utf-8")
        answer = None
        answer_metadata = None
    elif route == "D2A_GENERAL_LEARNING":
        status_raw = _role(raws, "general_review_status")
        facts_raw = _role(raws, "general_facts")
        unit_raw = _role(raws, "general_unit")
        lesson_raw = _role(raws, "lesson_markdown")
        answer_raw = _role(raws, "answer_book_markdown")
        status = _canonical_artifact(status_raw, "general-review-status")
        facts = _canonical_artifact(facts_raw, "general-unit-facts")
        unit = _canonical_artifact(unit_raw, "general-learning-unit")
        _source_binding(status, plan)
        _source_binding(facts, plan)
        selected_unit = facts.get("selected_unit")
        if selected_unit != identity:
            _fail("UNIT_BINDING_INVALID")
        if (
            status.get("review_state") != "REVIEWED_DRAFT"
            or status.get("selected_unit_id") != identity["id"]
            or status.get("selected_unit_origin") != identity["origin"]
            or status.get("selected_unit_scope") != identity["scope"]
            or status.get("facts_sha256") != sha256(facts_raw)
            or status.get("unit_artifact_sha256") != sha256(unit_raw)
            or status.get("lesson_markdown_sha256") != sha256(lesson_raw)
            or status.get("answer_book_markdown_sha256") != sha256(answer_raw)
            or status.get("review_session_sha256") != sha256(_role(raws, "general_review_session"))
            or status.get("writer_packet_sha256") != sha256(_role(raws, "writer_packet"))
            or status.get("candidate_sha256") != sha256(_role(raws, "candidate"))
            or status.get("factuality_report_sha256") != sha256(_role(raws, "factuality_report"))
            or status.get("beginner_report_sha256") != sha256(_role(raws, "beginner_report"))
            or unit.get("selected_unit") != identity
        ):
            _fail("REVIEW_STATUS_BINDING_INVALID")
        body = lesson_raw.decode("utf-8")
        answer = answer_raw.decode("utf-8")
        answer_metadata = None
    else:
        _fail("ROUTE_UNSUPPORTED")
    try:
        status_entry = next(item for item in records if item["role"] == (
            "chapter_review_status" if route == "PHASE6A_PROJECT_CLAIM" else "general_review_status"
        ))
    except StopIteration as exc:
        raise WholeBookWorkflowError("REVIEW_STATUS_BINDING_INVALID") from exc
    if status_entry["sha256"] != sha256(status_raw):
        _fail("REVIEW_STATUS_BINDING_INVALID")
    if route == "D2A_GENERAL_LEARNING":
        answer_metadata = {"general_answer_book_sha256": sha256(_role(raws, "answer_book_markdown"))}
    path_raws = {paths[record["role"]]: _role(raws, record["role"]) for record in records}
    return body, answer, {
        "status": status,
        "status_sha256": sha256(status_raw),
        "paths": paths,
        "path_raws": path_raws,
        "answer_metadata": answer_metadata,
    }


def _project_answer(
    inputs: Any, plan: Mapping[str, Any], unit_id: str, chapter_dir: Path, chapter_review_dir: Path,
    candidate_path: Path, answer_dir: Path, review_dir: Path | None,
) -> tuple[str, dict[str, Any], Any, dict[str, bytes] | None, dict[Path, bytes]]:
    try:
        package, candidate, candidate_raw, bank, bank_raw, book_raw = d1._authenticate_answer_book(
            inputs, chapter_dir=chapter_dir, chapter_review_dir=chapter_review_dir,
            answer_candidates=candidate_path, answer_dir=answer_dir,
        )
        facts = package.context.facts
        _source_binding(facts, plan)
        if facts.get("selected_unit", {}).get("curriculum_unit_id") != unit_id:
            _fail("ANSWER_BINDING_INVALID")
        metadata: dict[str, Any] = {
            "candidate_sha256": sha256(candidate_raw),
            "exercise_bank_sha256": sha256(bank_raw),
            "answer_book_sha256": sha256(book_raw),
            "d1b_review_state": "NOT_RUN",
            "d1b_status_sha256": None,
            "d1b_evidence_report_sha256": None,
            "d1b_beginner_report_sha256": None,
        }
        review_raws = None
        if review_dir is not None:
            # The explicit status-bearing D1b package is rebuilt against this exact D1 output.
            review_raws = d1._verify_review_directory(Path(review_dir), include_status=True)
            _session, _evidence, _beginner, status_raw = d1._validate_review_package(
                Path(review_dir), review_raws, package, candidate, candidate_raw, bank, bank_raw, book_raw,
            )
            status = _canonical_artifact(status_raw, "answer-review-status")
            if status.get("review_state") != "REVIEWED_DRAFT":
                _fail("ANSWER_REVIEW_NOT_COMPLETE")
            metadata["d1b_review_state"] = "REVIEWED_DRAFT"
            metadata["d1b_status_sha256"] = sha256(status_raw)
            metadata["d1b_evidence_report_sha256"] = sha256(review_raws["answer-evidence-review.json"])
            metadata["d1b_beginner_report_sha256"] = sha256(review_raws["answer-beginner-review.json"])
        package.recheck()
        d1_raws = d1._read_answer_directory(Path(answer_dir))
        return book_raw.decode("utf-8"), metadata, package, review_raws, {
            Path(candidate_path): candidate_raw,
            **{Path(answer_dir) / name: raw for name, raw in d1_raws.items()},
        }
    except WholeBookWorkflowError:
        raise
    except (d1.AnswerReviewWorkflowError, Phase6ReviewError, Phase6ChapterError, ArtifactValidationError, OSError, ValueError, TypeError) as exc:
        code = getattr(exc, "code", "ANSWER_BINDING_INVALID")
        raise WholeBookWorkflowError(code) from exc


def _inside(path: Path, base: Path) -> bool:
    try:
        return os.path.commonpath((os.path.normcase(str(path)), os.path.normcase(str(base)))) == os.path.normcase(str(base))
    except ValueError:
        return False


def _assert_output_disjoint(out_dir: Path, *inputs: Path) -> None:
    for path in inputs:
        absolute = path.absolute()
        if out_dir == absolute or _inside(out_dir, absolute) or _inside(absolute, out_dir):
            _fail("OUTPUT_INVALID")


def assemble_whole_book(
    inputs: Any, *, plan_path: Path, state_dir: Path, out_dir: Path,
    selected_attempts: Mapping[str, str],
    project_answer_candidates: Mapping[str, Path] | None = None,
    project_answer_dirs: Mapping[str, Path] | None = None,
    project_answer_review_dirs: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    candidates = dict(project_answer_candidates or {})
    answer_dirs = dict(project_answer_dirs or {})
    review_dirs = dict(project_answer_review_dirs or {})
    if set(candidates) != set(answer_dirs) or not set(review_dirs).issubset(candidates):
        _fail("ANSWER_SELECTION_INVALID")
    output = chapter._check_external_output(out_dir, inputs, must_exist=False)
    plan_file = plan_path.absolute()
    state_path = state_dir.absolute()
    _assert_output_disjoint(output, plan_file, state_path, *candidates.values(), *answer_dirs.values(), *review_dirs.values())
    try:
        authenticated_plan = planning.authenticate_curriculum_run_plan(inputs, plan_path=plan_path)
    except (planning.Phase6CurriculumRunError, Phase6ChapterError, ArtifactValidationError, OSError, ValueError, TypeError) as exc:
        raise WholeBookWorkflowError(getattr(exc, "code", "PLAN_AUTHENTICATION_FAILED")) from exc
    plan, plan_raw, checked_plan_path = authenticated_plan.plan, authenticated_plan.raw, authenticated_plan.path
    try:
        directory = execution._external_state_dir(inputs, state_dir, must_exist=True)
        states, state_raws = load_state_chain(directory, plan, plan_raw)
        head = states[-1]
        validate_state_against_plan(head, plan, plan_raw)
        rows = validate_whole_book_selection(plan, states, selected_attempts)
    except (CurriculumRunStateError, WholeBookError) as exc:
        raise WholeBookWorkflowError(getattr(exc, "code", "STATE_INVALID")) from exc
    plan_by_id = {row["unit_identity"]["id"]: row for row in plan["units"]}
    state_by_id = {row["unit_id"]: row for row in head["units"]}
    bodies: dict[str, str] = {}
    answers: dict[str, str] = {}
    selected_metadata: dict[str, dict[str, Any]] = {}
    project_answer_metadata: dict[str, dict[str, Any]] = {}
    answer_packages: list[tuple[Any, dict[str, bytes] | None, Path | None, dict[Path, bytes]]] = []
    expected_bytes: dict[Path, bytes] = {}
    for unit_id, attempt_id in selected_attempts.items():
        plan_row = plan_by_id[unit_id]
        state_row = state_by_id[unit_id]
        try:
            body, answer, metadata = _verify_selected_attempt(
                plan, head, directory, plan_row, state_row, attempt_id,
            )
        except WholeBookWorkflowError:
            raise
        bodies[unit_id] = body
        selected_metadata[unit_id] = metadata
        if answer is not None:
            answers[unit_id] = answer
        expected_bytes.update(metadata["path_raws"])
        if unit_id in candidates:
            if plan_row["route"] != "PHASE6A_PROJECT_CLAIM":
                _fail("ANSWER_SELECTION_INVALID")
            try:
                body_answer, answer_metadata, package, review_raws, answer_raws = _project_answer(
                    inputs, plan, unit_id,
                    Path(state_dir) / f"units/{plan_row['position']:04d}/{attempt_id}/phase6a",
                    Path(state_dir) / f"units/{plan_row['position']:04d}/{attempt_id}/phase6a-review",
                    candidates[unit_id], answer_dirs[unit_id], review_dirs.get(unit_id),
                )
            except WholeBookWorkflowError:
                raise
            answers[unit_id] = body_answer
            project_answer_metadata[unit_id] = answer_metadata
            answer_packages.append((package, review_raws, review_dirs.get(unit_id), answer_raws))
            expected_bytes.update(answer_raws)
            if unit_id in review_dirs:
                expected_bytes.update({Path(review_dirs[unit_id]) / name: raw for name, raw in review_raws.items()})
    whole_book = render_whole_book(plan, rows, bodies).encode("utf-8")
    answer_book = render_answer_book(rows, answers).encode("utf-8")
    quality = render_quality_and_gaps(
        plan, rows,
        project_answer_states={
            unit_id: {"answer_status": "INCLUDED", **metadata}
            for unit_id, metadata in project_answer_metadata.items()
        },
    ).encode("utf-8")
    output_bytes = {
        "WHOLE-BOOK.md": whole_book,
        "WHOLE-ANSWER-BOOK.md": answer_book,
        "QUALITY-AND-GAPS.md": quality,
    }
    manifest = build_whole_book_manifest(
        plan, plan_raw, head, state_raws[-1], rows, output_bytes,
        project_answers=project_answer_metadata,
    )
    manifest_raw = dumps_artifact(manifest).encode("utf-8")
    try:
        authenticated_plan.recheck()
        if load_state_chain(directory, plan, plan_raw)[1] != state_raws:
            _fail("STATE_CHANGED")
        for path, expected in expected_bytes.items():
            chapter._assert_no_link_components(path)
            if chapter._read_review_file(path) != expected:
                _fail("INPUT_CHANGED")
        for package, review_raws, review_dir, _answer_raws in answer_packages:
            package.recheck()
            if review_dir is not None:
                current = d1._verify_review_directory(review_dir, include_status=True)
                if current != review_raws:
                    _fail("INPUT_CHANGED")
    except WholeBookWorkflowError:
        raise
    except (planning.Phase6CurriculumRunError, CurriculumRunStateError, d1.AnswerReviewWorkflowError, Phase6ChapterError, Phase6ReviewError, OSError) as exc:
        raise WholeBookWorkflowError(getattr(exc, "code", "INPUT_CHANGED")) from exc
    output = chapter._check_external_output(out_dir, inputs, must_exist=False)
    _assert_output_disjoint(output, plan_file, state_path, *candidates.values(), *answer_dirs.values(), *review_dirs.values())
    try:
        output.mkdir()
    except FileExistsError as exc:
        raise WholeBookWorkflowError("OUTPUT_EXISTS") from exc
    except OSError as exc:
        raise WholeBookWorkflowError("OUTPUT_INVALID") from exc
    try:
        for name in ("WHOLE-BOOK.md", "WHOLE-ANSWER-BOOK.md", "QUALITY-AND-GAPS.md"):
            chapter._publish_new_file(output / name, output_bytes[name])
        for name, expected in output_bytes.items():
            if chapter._read_review_file(output / name) != expected:
                _fail("OUTPUT_INTEGRITY_INVALID")
        chapter._publish_new_file(output / "whole-book-assembly.json", manifest_raw)
    except (Phase6ChapterError, WholeBookWorkflowError) as exc:
        raise WholeBookWorkflowError(getattr(exc, "code", "OUTPUT_FAILED")) from exc
    return manifest


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--phase4c-package", required=True, type=Path)
    parser.add_argument("--claim-candidates", required=True, type=Path)
    parser.add_argument("--claim-evidence", required=True, type=Path)
    parser.add_argument("--claim-evidence-graph", required=True, type=Path)
    parser.add_argument("--prerequisite-candidates", required=True, type=Path)
    parser.add_argument("--prerequisite-graph", required=True, type=Path)
    parser.add_argument("--curriculum-candidates", required=True, type=Path)
    parser.add_argument("--curriculum", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    assemble = parser.add_subparsers(dest="command", required=True).add_parser("assemble")
    _add_inputs(assemble)
    assemble.add_argument("--plan", required=True, type=Path)
    assemble.add_argument("--state-dir", required=True, type=Path)
    assemble.add_argument("--out-dir", required=True, type=Path)
    assemble.add_argument("--select", action="append", default=[])
    assemble.add_argument("--project-answer-candidate", action="append", default=[])
    assemble.add_argument("--project-answer-dir", action="append", default=[])
    assemble.add_argument("--project-answer-review-dir", action="append", default=[])
    return parser


def _inputs(args: argparse.Namespace):
    return planning._inputs(args)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        selected = {}
        for value in args.select:
            unit_id, separator, attempt_id = value.partition("=")
            if not separator or not unit_id or not attempt_id or unit_id in selected:
                _fail("SELECTION_INVALID")
            selected[unit_id] = attempt_id
        manifest = assemble_whole_book(
            _inputs(args), plan_path=args.plan, state_dir=args.state_dir, out_dir=args.out_dir,
            selected_attempts=selected,
            project_answer_candidates=_parse_pairs(args.project_answer_candidate),
            project_answer_dirs=_parse_pairs(args.project_answer_dir),
            project_answer_review_dirs=_parse_pairs(args.project_answer_review_dir),
        )
        print(json.dumps({
            "status": manifest["overall_status"],
            "assembly_id": manifest["assembly_id"],
            "selected_count": manifest["selected_count"],
            "unit_count": manifest["unit_count"],
            "source_status": manifest["source_status"],
            "unknown_files": manifest["unknown_files"],
            "cross_chapter_audit": "NOT_RUN",
        }, separators=(",", ":")))
        return 0
    except WholeBookWorkflowError as exc:
        print(json.dumps({"error": exc.code}, separators=(",", ":")))
        return 2
    except WholeBookError as exc:
        print(json.dumps({"error": exc.code}, separators=(",", ":")))
        return 2
    except (planning.Phase6CurriculumRunError, CurriculumRunStateError, d1.AnswerReviewWorkflowError, Phase6ReviewError, Phase6ChapterError, ArtifactValidationError, OSError, ValueError, TypeError) as exc:
        print(json.dumps({"error": getattr(exc, "code", "INPUT_INVALID")}, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
