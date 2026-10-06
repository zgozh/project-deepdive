#!/usr/bin/env python3
"""Explicit, provider-free D2B2 start/advance/resume/repair coordinator."""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, _strict_json_loads, dumps_artifact, validate_artifact
import chapter_review_workflow as b1
import curriculum_run_workflow as planning
import general_learning_review_workflow as d2a2
import general_unit_workflow as d2a
import phase6_chapter as chapter
import phase6_curriculum_repair as repair
from phase6_curriculum_repair import classify_repair_findings
from phase6_curriculum_run_state import (
    CurriculumRunStateError,
    advance_revision,
    create_state_dir,
    initial_state,
    load_state_chain,
    publish_revision,
    sha256,
    upgrade_to_v1_1,
    validate_state_against_plan,
)
from phase6_general_learning import Phase6GeneralLearningError
from phase6_review import Phase6ReviewError
from phase6_general_review import Phase6GeneralReviewError


class CurriculumRunExecutionError(ValueError):
    """A fixed, redacted run-execution failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise CurriculumRunExecutionError(code)


def _load_plan(inputs: chapter.ChapterInputPaths, plan_path: Path) -> tuple[dict[str, Any], bytes, Path]:
    """Read/validate a canonical plan without taking a source snapshot."""
    try:
        return planning.read_canonical_curriculum_run_plan(inputs, plan_path)
    except CurriculumRunExecutionError:
        raise
    except (ArtifactValidationError, UnicodeError, ValueError, TypeError, chapter.Phase6ChapterError) as exc:
        raise CurriculumRunExecutionError("PLAN_INVALID") from exc


def _external_state_dir(inputs: chapter.ChapterInputPaths, path: Path, *, must_exist: bool) -> Path:
    try:
        return chapter._check_external_output(path, inputs, must_exist=must_exist)
    except chapter.Phase6ChapterError as exc:
        raise CurriculumRunExecutionError(exc.code) from exc


def _mkdir_child(parent: Path, child: Path) -> None:
    chapter._assert_no_link_components(parent)
    if child.exists() or os.path.lexists(child):
        if child.is_symlink() or not child.is_dir():
            _fail("ATTEMPT_PATH_INVALID")
        return
    try:
        child.mkdir()
    except FileExistsError:
        if child.is_symlink() or not child.is_dir():
            _fail("ATTEMPT_PATH_INVALID")
    except OSError as exc:
        raise CurriculumRunExecutionError("ATTEMPT_PATH_INVALID") from exc


def _attempt_dir(state_dir: Path, row: Mapping[str, Any], *, create: bool = False) -> Path:
    base = state_dir / "units" / f"{row['position']:04d}"
    attempt_id = row.get("attempt_id") or "attempt-0001"
    if attempt_id not in {"attempt-0001", "attempt-0002", "attempt-0003"}:
        _fail("ATTEMPT_PATH_INVALID")
    attempt = base / attempt_id
    if create:
        _mkdir_child(state_dir, state_dir / "units")
        _mkdir_child(state_dir / "units", base)
        _mkdir_child(base, attempt)
    else:
        for path in (state_dir / "units", base, attempt):
            if os.path.lexists(path) and (path.is_symlink() or not path.is_dir()):
                _fail("ATTEMPT_PATH_INVALID")
    return attempt


def _unit_paths(state_dir: Path, row: Mapping[str, Any], *, create: bool = False) -> dict[str, Path]:
    attempt = _attempt_dir(state_dir, row, create=create)
    if row["route"] == "PHASE6A_PROJECT_CLAIM":
        return {"attempt": attempt, "prepare": attempt / "phase6a", "unit": attempt / "phase6a", "review": attempt / "phase6a-review"}
    if row["route"] == "D2A_GENERAL_LEARNING":
        return {"attempt": attempt, "prepare": attempt / "general-prepare", "unit": attempt / "general-unit", "review": attempt / "general-review"}
    _fail("ROUTE_UNSUPPORTED")


def _sync_active_attempt(row: dict[str, Any], schema_version: str) -> None:
    """Mirror one completed boundary into the current v1.1 attempt record."""
    if schema_version != "1.1.0":
        return
    attempts = row["attempts"]
    if not attempts:
        if row["attempt_id"] != "attempt-0001":
            return
        attempts.append({
            "attempt_id": "attempt-0001", "execution_state": row["execution_state"],
            "disposition": "ORIGINAL", "predecessor_attempt_id": None,
            "predecessor_review_status_sha256": None, "predecessor_report_digests": [],
            "artifact_digests": [],
        })
    active = attempts[-1]
    if row["attempt_id"] != active["attempt_id"]:
        _fail("STATE_BINDING_INVALID")
    if row["execution_state"] in {
        "REPAIR_READY", "PREPARED", "BUILT_DRAFT", "AWAITING_REVIEW", "REVIEWED_DRAFT", "BLOCKED_REVIEW",
        "REVIEW_INCOMPLETE_STOP",
    }:
        active["execution_state"] = row["execution_state"]
        attempt_marker = f"/{active['attempt_id']}/"
        active["artifact_digests"] = [
            copy.deepcopy(item) for item in row["artifact_digests"]
            if attempt_marker in item["relative_path"]
        ]


def _record(state_dir: Path, role: str, path: Path) -> dict[str, str]:
    chapter._assert_no_link_components(path)
    if path.is_symlink() or not path.is_file():
        _fail("OUTPUT_INTEGRITY_INVALID")
    try:
        relative = path.relative_to(state_dir).as_posix()
        raw = chapter._read_review_file(path)
    except (ValueError, chapter.Phase6ChapterError) as exc:
        raise CurriculumRunExecutionError("OUTPUT_INTEGRITY_INVALID") from exc
    if not relative.startswith("units/"):
        _fail("OUTPUT_INTEGRITY_INVALID")
    return {"role": role, "relative_path": relative, "sha256": sha256(raw)}


def _verify_recorded_outputs(state_dir: Path, rows: list[Mapping[str, Any]]) -> None:
    for row in rows:
        for entry in row["artifact_digests"]:
            path = state_dir.joinpath(*entry["relative_path"].split("/"))
            chapter._assert_no_link_components(path)
            if path.is_symlink() or not path.is_file():
                _fail("OUTPUT_DRIFT")
            try:
                digest = sha256(path.read_bytes())
            except OSError as exc:
                raise CurriculumRunExecutionError("OUTPUT_DRIFT") from exc
            if digest != entry["sha256"]:
                _fail("OUTPUT_DRIFT")


def _source_binding(value: Mapping[str, Any], plan: Mapping[str, Any], *, require_inputs: bool = True) -> None:
    fields = ["repository_revision", "snapshot_kind", "source_metadata", "source_status",
              "source_run_manifest_sha256", "unknown_files"]
    if require_inputs:
        fields.append("input_digests")
    if any(value.get(key) != plan.get(key) for key in fields):
        _fail("PLAN_STALE")


def _unit_identity(plan_row: Mapping[str, Any], unit_id: str) -> None:
    if plan_row["unit_identity"]["id"] != unit_id:
        _fail("UNIT_BINDING_INVALID")


def _facts_from_file(path: Path, kind: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = chapter._read_review_file(path)
        value = _strict_json_loads(raw.decode("utf-8"))
        validate_artifact(value)
    except (chapter.Phase6ChapterError, ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise CurriculumRunExecutionError("PREPARED_OUTPUT_INVALID") from exc
    if not isinstance(value, dict) or value.get("artifact_kind") != kind or dumps_artifact(value).encode("utf-8") != raw:
        _fail("PREPARED_OUTPUT_INVALID")
    return value, raw


def _row_index(state: Mapping[str, Any], unit_id: str) -> int:
    for index, row in enumerate(state["units"]):
        if row["unit_id"] == unit_id:
            return index
    _fail("STATE_BINDING_INVALID")


def _state_result(state: Mapping[str, Any], action: str = "NONE") -> dict[str, Any]:
    return {
        "run_state": state["run_state"], "run_state_id": state["run_state_id"],
        "state_revision": state["state_revision"], "action": action,
    }


def _persist_next(
    inputs: chapter.ChapterInputPaths, plan_path: Path, state_dir: Path,
    plan: Mapping[str, Any], plan_raw: bytes, states: list[dict[str, Any]], raws: list[bytes],
    rows: list[dict[str, Any]], *, action: str,
) -> dict[str, Any]:
    if chapter._read_review_file(plan_path) != plan_raw:
        _fail("PLAN_CHANGED")
    latest = states[-1]
    next_state = advance_revision(latest, raws[-1], rows)
    validate_state_against_plan(next_state, plan, plan_raw)
    publish_revision(state_dir, next_state)
    return _state_result(next_state, action)


def start_curriculum_run(
    inputs: chapter.ChapterInputPaths, *, plan_path: Path, state_dir: Path,
) -> dict[str, Any]:
    context = planning.authenticate_curriculum_run_plan(inputs, plan_path=plan_path)
    output = _external_state_dir(inputs, state_dir, must_exist=False)
    directory = create_state_dir(output)
    context.recheck()
    state = initial_state(context.plan, context.raw)
    publish_revision(directory, state)
    return _state_result(state, "STARTED")


def _dependency_states(row: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> tuple[bool, bool]:
    by_id = {item["unit_id"]: item for item in rows}
    prerequisites = [by_id[unit_id] for unit_id in row["prerequisite_ids"]]
    blocked = any(item["execution_state"] in {
        "BLOCKED_UPSTREAM", "BLOCKED_PREREQUISITE", "BLOCKED_REVIEW", "UPSTREAM_REQUIRED",
        "STOP_UNSUPPORTED", "REVIEW_INCOMPLETE_STOP", "REPAIR_LIMIT_REACHED",
    } for item in prerequisites)
    ready = all(item["execution_state"] == "REVIEWED_DRAFT" for item in prerequisites)
    return ready, blocked


def _expected_action(row: Mapping[str, Any], paths: Mapping[str, Path]) -> tuple[str | None, str | None]:
    if row["execution_state"] in {"PLANNED", "REPAIR_READY"}:
        if row["route"] not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
            return None, "BLOCKED_UPSTREAM"
        if os.path.lexists(paths["prepare"]):
            _fail("ATTEMPT_RECONCILIATION_REQUIRED")
        return "PREPARE", None
    if row["execution_state"] == "PREPARED":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            candidate = paths["prepare"] / "chapter.md"
            outputs = (paths["prepare"] / "exercise-bank.json", paths["prepare"] / "chapter-draft-status.json")
        else:
            candidate = paths["prepare"] / "general-unit-candidate.json"
            outputs = (paths["unit"] / "general-learning-unit.json", paths["unit"] / "general-answer-book.md")
        if any(os.path.lexists(path) for path in outputs):
            _fail("ATTEMPT_RECONCILIATION_REQUIRED")
        if not candidate.is_file():
            return None, "WAITING_FOR_WRITER"
        return "BUILD", None
    if row["execution_state"] == "BUILT_DRAFT":
        if os.path.lexists(paths["review"]):
            _fail("ATTEMPT_RECONCILIATION_REQUIRED")
        return "PREPARE_REVIEW", None
    if row["execution_state"] == "AWAITING_REVIEW":
        required = (
            ("evidence-review.json", "beginner-review.json")
            if row["route"] == "PHASE6A_PROJECT_CLAIM"
            else ("general-factuality-review.json", "general-beginner-review.json")
        )
        if os.path.lexists(paths["review"] / "general-review-status.json") or os.path.lexists(paths["review"] / "chapter-review-status.json"):
            _fail("ATTEMPT_RECONCILIATION_REQUIRED")
        if not all((paths["review"] / name).is_file() for name in required):
            return None, "WAITING_FOR_REVIEW"
        return "FINALIZE_REVIEW", None
    return None, None


def _candidate_preflight(inputs: chapter.ChapterInputPaths, row: Mapping[str, Any], paths: Mapping[str, Path], state_dir: Path) -> None:
    facts, _raw = _facts_from_file(
        paths["prepare"] / ("chapter-facts.json" if row["route"] == "PHASE6A_PROJECT_CLAIM" else "general-unit-facts.json"),
        "chapter-facts" if row["route"] == "PHASE6A_PROJECT_CLAIM" else "general-unit-facts",
    )
    if row["route"] == "PHASE6A_PROJECT_CLAIM":
        candidate_raw = chapter._read_review_file(paths["prepare"] / "chapter.md")
        _unit_identity(row["plan_row"], facts["selected_unit"]["curriculum_unit_id"])
        try:
            _bank, _checks, errors = chapter._verify_markdown(candidate_raw, facts, inputs)
        except Exception:
            errors = ["MARKDOWN_INVALID"]
        if _bank is None or errors:
            _fail("CANDIDATE_INVALID")


def _execute_boundary(
    inputs: chapter.ChapterInputPaths, plan: Mapping[str, Any], plan_raw: bytes,
    state_dir: Path, states: list[dict[str, Any]], raws: list[bytes],
    row_index: int, action: str, plan_path: Path,
) -> dict[str, Any]:
    state = states[-1]
    rows = copy.deepcopy(state["units"])
    row = rows[row_index]
    plan_row = plan["units"][row_index]
    row_with_plan = {**row, "plan_row": plan_row}
    paths = _unit_paths(state_dir, row, create=action in {"PREPARE", "PREPARE_REVIEW"})
    unit_id = row["unit_id"]
    if action == "PREPARE":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            facts, _packet = chapter.prepare_chapter_facts(inputs, unit_id=unit_id, out_dir=paths["prepare"])
            _source_binding(facts, plan)
            _unit_identity(plan_row, facts["selected_unit"]["curriculum_unit_id"])
            pairs = (("chapter_facts", paths["prepare"] / "chapter-facts.json"),
                     ("writer_packet", paths["prepare"] / "writer-packet.md"))
        else:
            facts, _packet = d2a.prepare_general_unit(inputs, unit_id=unit_id, out_dir=paths["prepare"])
            _source_binding(facts, plan)
            _unit_identity(plan_row, facts["selected_unit"]["id"])
            pairs = (("general_facts", paths["prepare"] / "general-unit-facts.json"),
                     ("writer_packet", paths["prepare"] / "general-unit-writer-packet.md"))
        if row["attempt_id"] is None:
            row["attempt_id"] = "attempt-0001"
        row["artifact_digests"].extend(_record(state_dir, role, path) for role, path in pairs)
        row["execution_state"] = "PREPARED"
        _sync_active_attempt(row, state["schema_version"])
        return _persist_next(inputs, plan_path, state_dir, plan, plan_raw, states, raws, rows, action=f"PREPARED:{unit_id}")

    if action == "BUILD":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            try:
                _candidate_preflight(inputs, row_with_plan, paths, state_dir)
            except CurriculumRunExecutionError as exc:
                if exc.code != "CANDIDATE_INVALID":
                    raise
                planning.authenticate_curriculum_run_plan(inputs, plan_path=plan_path)
                raise
            markdown = paths["prepare"] / "chapter.md"
            try:
                _bank, _status = chapter.verify_chapter_draft(
                    inputs, unit_id=unit_id, facts_path=paths["prepare"] / "chapter-facts.json",
                    markdown_path=markdown, out_dir=paths["prepare"],
                )
            except chapter.Phase6ChapterError as exc:
                raise CurriculumRunExecutionError("CANDIDATE_INVALID" if exc.code in {"MARKDOWN_FORMAT_INVALID", "HEADING_ORDER_INVALID", "CLAIM_MARKER_INVALID", "EXERCISE_INVALID", "EXERCISE_SYNC_INVALID", "EXCERPT_INVALID"} else exc.code) from exc
            row["artifact_digests"].extend((
                _record(state_dir, "chapter_markdown", markdown),
                _record(state_dir, "exercise_bank", paths["prepare"] / "exercise-bank.json"),
                _record(state_dir, "chapter_draft_status", paths["prepare"] / "chapter-draft-status.json"),
            ))
        else:
            try:
                artifact = d2a.build_general_unit(
                    inputs, unit_id=unit_id, facts_path=paths["prepare"] / "general-unit-facts.json",
                    candidate_path=paths["prepare"] / "general-unit-candidate.json", out_dir=paths["unit"],
                )
            except Phase6GeneralLearningError as exc:
                if exc.code in {"CANDIDATE_INVALID", "UNIT_METADATA_INVALID", "PROVENANCE_MISMATCH", "CONCEPT_COVERAGE_INVALID"}:
                    raise CurriculumRunExecutionError("CANDIDATE_INVALID") from exc
                raise CurriculumRunExecutionError(exc.code) from exc
            _source_binding(artifact, plan)
            row["artifact_digests"].extend((
                _record(state_dir, "candidate", paths["prepare"] / "general-unit-candidate.json"),
                _record(state_dir, "general_unit", paths["unit"] / "general-learning-unit.json"),
                _record(state_dir, "lesson_markdown", paths["unit"] / "general-learning-unit.md"),
                _record(state_dir, "answer_book_markdown", paths["unit"] / "general-answer-book.md"),
            ))
        row["execution_state"] = "BUILT_DRAFT"
        _sync_active_attempt(row, state["schema_version"])
        return _persist_next(inputs, plan_path, state_dir, plan, plan_raw, states, raws, rows, action=f"BUILT_DRAFT:{unit_id}")

    if action == "PREPARE_REVIEW":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            session = b1.prepare_chapter_review(
                inputs, chapter_dir=paths["prepare"], review_dir=paths["review"], writer_alias="d2b2-writer",
            )
            _source_binding(session, plan)
            if session["chapter_facts_sha256"] != next(item["sha256"] for item in row["artifact_digests"] if item["role"] == "chapter_facts"):
                _fail("OUTPUT_INTEGRITY_INVALID")
            session_path = paths["review"] / "chapter-review-session.json"
            session_role = "chapter_review_session"
        else:
            d2a2.prepare_general_unit_review(
                inputs, prepare_dir=paths["prepare"], unit_dir=paths["unit"], review_dir=paths["review"],
            )
            session_path = paths["review"] / "general-review-session.json"
            session, _raw = _facts_from_file(session_path, "general-review-session")
            _source_binding(session, plan)
            if session["facts_sha256"] != next(item["sha256"] for item in row["artifact_digests"] if item["role"] == "general_facts"):
                _fail("OUTPUT_INTEGRITY_INVALID")
            session_role = "general_review_session"
        row["artifact_digests"].append(_record(state_dir, session_role, session_path))
        row["execution_state"] = "AWAITING_REVIEW"
        _sync_active_attempt(row, state["schema_version"])
        return _persist_next(inputs, plan_path, state_dir, plan, plan_raw, states, raws, rows, action=f"AWAITING_REVIEW:{unit_id}")

    if action == "FINALIZE_REVIEW":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            status = b1.finalize_chapter_review(
                inputs, chapter_dir=paths["prepare"], review_dir=paths["review"],
                evidence_report_path=paths["review"] / "evidence-review.json",
                beginner_report_path=paths["review"] / "beginner-review.json",
                out_status=paths["review"] / "chapter-review-status.json",
            )
            _source_binding(status, plan, require_inputs=False)
            report_pairs = (
                ("evidence_report", paths["review"] / "evidence-review.json"),
                ("beginner_report", paths["review"] / "beginner-review.json"),
            )
            status_path = paths["review"] / "chapter-review-status.json"
        else:
            status = d2a2.finalize_general_unit_review(
                inputs, prepare_dir=paths["prepare"], unit_dir=paths["unit"], review_dir=paths["review"],
                out_status=paths["review"] / "general-review-status.json",
            )
            _source_binding(status, plan)
            report_pairs = (
                ("factuality_report", paths["review"] / "general-factuality-review.json"),
                ("beginner_report", paths["review"] / "general-beginner-review.json"),
            )
            status_path = paths["review"] / "general-review-status.json"
        row["artifact_digests"].extend(_record(state_dir, role, path) for role, path in report_pairs)
        row["artifact_digests"].append(_record(state_dir, "general_review_status" if row["route"] == "D2A_GENERAL_LEARNING" else "chapter_review_status", status_path))
        review_state = status["review_state"]
        if review_state == "REVIEWED_DRAFT":
            row["execution_state"] = "REVIEWED_DRAFT"
        elif review_state == "REVIEW_INCOMPLETE" and state["schema_version"] == "1.1.0":
            row["execution_state"] = "REVIEW_INCOMPLETE_STOP"
            row["repair_disposition"] = "REVIEW_INCOMPLETE"
        else:
            row["execution_state"] = "BLOCKED_REVIEW"
        _sync_active_attempt(row, state["schema_version"])
        return _persist_next(inputs, plan_path, state_dir, plan, plan_raw, states, raws, rows, action=f"{row['execution_state']}:{unit_id}")
    _fail("ACTION_INVALID")


def _select_action(state: Mapping[str, Any], state_dir: Path) -> tuple[int | None, str | None, str | None, bool]:
    rows = state["units"]
    waiting = None
    for index, row in enumerate(rows):
        if row["route"] not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
            continue
        if row.get("attempt_id") in {"attempt-0002", "attempt-0003"} or row["execution_state"] == "REPAIR_READY":
            continue
        paths = _unit_paths(state_dir, row)
        if row["execution_state"] in {"PREPARED", "BUILT_DRAFT", "AWAITING_REVIEW"}:
            action, wait = _expected_action(row, paths)
            if action:
                return index, action, None, False
            if wait:
                waiting = waiting or wait
    for index, row in enumerate(rows):
        if row["execution_state"] != "PLANNED":
            continue
        if row["route"] not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
            continue
        ready, blocked = _dependency_states(row, rows)
        if blocked:
            return index, "BLOCK_PREREQUISITE", None, False
        if not ready:
            waiting = waiting or "WAITING_FOR_PREREQUISITES"
            continue
        action, wait = _expected_action(row, _unit_paths(state_dir, row))
        if action:
            return index, action, None, False
        if wait:
            waiting = waiting or wait
    return None, None, waiting, True


def advance_curriculum_run(
    inputs: chapter.ChapterInputPaths, *, plan_path: Path, state_dir: Path,
) -> dict[str, Any]:
    plan, raw_plan, checked_plan_path = _load_plan(inputs, plan_path)
    directory = _external_state_dir(inputs, state_dir, must_exist=True)
    states, raws = load_state_chain(directory, plan, raw_plan)
    validate_state_against_plan(states[-1], plan, raw_plan)
    _verify_recorded_outputs(directory, states[-1]["units"])
    index, action, waiting, is_waiting = _select_action(states[-1], directory)
    if is_waiting:
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        return _state_result(states[-1], waiting or "NO_ACTION")
    if action == "BLOCK_PREREQUISITE":
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        rows = copy.deepcopy(states[-1]["units"])
        rows[index]["execution_state"] = "BLOCKED_PREREQUISITE"
        return _persist_next(inputs, checked_plan_path, directory, plan, raw_plan, states, raws, rows,
                             action=f"BLOCKED_PREREQUISITE:{rows[index]['unit_id']}")
    assert index is not None and action is not None
    row = states[-1]["units"][index]
    paths = _unit_paths(directory, row)
    if action == "BUILD" and row["route"] == "D2A_GENERAL_LEARNING" and not (paths["prepare"] / "general-unit-candidate.json").is_file():
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        return _state_result(states[-1], "WAITING_FOR_WRITER")
    if action == "AWAITING_REVIEW":
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        return _state_result(states[-1], "WAITING_FOR_REVIEW")
    if action == "BUILD" and row["route"] == "PHASE6A_PROJECT_CLAIM":
        candidate = paths["prepare"] / "chapter.md"
        if not candidate.is_file():
            planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
            return _state_result(states[-1], "WAITING_FOR_WRITER")
    return _execute_boundary(inputs, plan, raw_plan, directory, states, raws, index, action, checked_plan_path)


def _bound_attempt_digest(attempt: Mapping[str, Any], role: str, raw: bytes) -> None:
    entries = [item for item in attempt["artifact_digests"] if item["role"] == role]
    if len(entries) != 1 or entries[0]["sha256"] != sha256(raw):
        _fail("PREDECESSOR_BINDING_INVALID")


def _authenticated_predecessor(
    inputs: chapter.ChapterInputPaths, plan: Mapping[str, Any], row: Mapping[str, Any],
    plan_row: Mapping[str, Any], state_dir: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, bytes]]:
    """Authenticate one finalized route package as the only source replay for repair triage."""
    paths = _unit_paths(state_dir, row)
    attempt = row["attempts"][-1]
    if row["execution_state"] != "BLOCKED_REVIEW" or attempt["execution_state"] != "BLOCKED_REVIEW":
        _fail("PREDECESSOR_STATUS_INVALID")
    if row["route"] == "PHASE6A_PROJECT_CLAIM":
        package = b1.authenticate_chapter_review_package(
            inputs, chapter_dir=paths["prepare"], review_dir=paths["review"],
        )
        facts = package.context.facts
        _source_binding(facts, plan)
        _unit_identity(plan_row, facts["selected_unit"]["curriculum_unit_id"])
        status = package.status
        raws = {
            "chapter_review_status": package.package_raw["chapter-review-status.json"],
            "evidence_report": package.package_raw["evidence-review.json"],
            "beginner_report": package.package_raw["beginner-review.json"],
        }
        reports = {"evidence": package.evidence_report, "beginner": package.beginner_report}
        package.recheck()
    elif row["route"] == "D2A_GENERAL_LEARNING":
        status = d2a2.authenticate_general_unit_review_status(
            inputs, prepare_dir=paths["prepare"], unit_dir=paths["unit"], review_dir=paths["review"],
        )
        _source_binding(status, plan)
        facts, facts_raw = _facts_from_file(paths["prepare"] / "general-unit-facts.json", "general-unit-facts")
        _source_binding(facts, plan)
        identity = plan_row["unit_identity"]
        selected = facts["selected_unit"]
        if ({key: selected.get(key) for key in identity} != identity
                or status["selected_unit_id"] != identity["id"]
                or status["selected_unit_origin"] != identity["origin"]
                or status["selected_unit_scope"] != identity["scope"]
                or status["facts_sha256"] != sha256(facts_raw)):
            _fail("UNIT_BINDING_INVALID")
        factuality, factuality_raw = d2a2._read_report(
            paths["review"] / "general-factuality-review.json", "general-factuality-review",
        )
        beginner, beginner_raw = d2a2._read_report(
            paths["review"] / "general-beginner-review.json", "general-beginner-review",
        )
        status_raw = chapter._read_review_file(paths["review"] / "general-review-status.json")
        if (status["factuality_report_sha256"] != sha256(factuality_raw)
                or status["beginner_report_sha256"] != sha256(beginner_raw)
                or status_raw != dumps_artifact(status).encode("utf-8")):
            _fail("PREDECESSOR_BINDING_INVALID")
        raws = {
            "general_review_status": status_raw,
            "factuality_report": factuality_raw,
            "beginner_report": beginner_raw,
        }
        reports = {"factuality": factuality, "beginner": beginner}
    else:
        _fail("ROUTE_UNSUPPORTED")

    status_role = "chapter_review_status" if row["route"] == "PHASE6A_PROJECT_CLAIM" else "general_review_status"
    for role, raw in raws.items():
        _bound_attempt_digest(attempt, role, raw)
    if status["review_state"] not in {"REPAIR_REQUIRED", "REVIEW_INCOMPLETE"}:
        _fail("PREDECESSOR_STATUS_INVALID")
    if status_role not in raws:
        _fail("PREDECESSOR_BINDING_INVALID")
    return status, reports, raws


def _publish_state_upgrade(
    inputs: chapter.ChapterInputPaths, plan_path: Path, state_dir: Path, plan: Mapping[str, Any], plan_raw: bytes,
    states: list[dict[str, Any]], raws: list[bytes],
) -> dict[str, Any]:
    authenticated = planning.authenticate_curriculum_run_plan(inputs, plan_path=plan_path)
    if authenticated.raw != plan_raw or authenticated.plan != plan:
        _fail("PLAN_CHANGED")
    upgraded = upgrade_to_v1_1(states[-1], raws[-1], plan, plan_raw)
    authenticated.recheck()
    if chapter._read_review_file(plan_path) != plan_raw:
        _fail("PLAN_CHANGED")
    publish_revision(state_dir, upgraded)
    return _state_result(upgraded, "STATE_V1_1_MIGRATED")


def _repair_candidate_row(state: Mapping[str, Any], unit_id: str | None) -> int | None:
    if unit_id is not None:
        index = _row_index(state, unit_id)
        row = state["units"][index]
        if row["route"] in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"} and (
            row["execution_state"] == "BLOCKED_REVIEW"
            or (row["attempt_id"] in {"attempt-0002", "attempt-0003"}
                and row["execution_state"] in {"REPAIR_READY", "PREPARED", "BUILT_DRAFT", "AWAITING_REVIEW"})
        ):
            return index
        return None
    for index, row in enumerate(state["units"]):
        if row["route"] not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
            continue
        if row["execution_state"] == "BLOCKED_REVIEW" or (
            row["attempt_id"] in {"attempt-0002", "attempt-0003"}
            and row["execution_state"] in {"REPAIR_READY", "PREPARED", "BUILT_DRAFT", "AWAITING_REVIEW"}
        ):
            return index
    return None


def advance_curriculum_repair(
    inputs: chapter.ChapterInputPaths, *, plan_path: Path, state_dir: Path,
    unit_id: str | None = None,
) -> dict[str, Any]:
    """Triage one finalized repair or advance one already-open repair boundary."""
    plan, raw_plan, checked_plan_path = _load_plan(inputs, plan_path)
    directory = _external_state_dir(inputs, state_dir, must_exist=True)
    states, raws = load_state_chain(directory, plan, raw_plan)
    validate_state_against_plan(states[-1], plan, raw_plan)
    _verify_recorded_outputs(directory, states[-1]["units"])
    if states[-1]["schema_version"] != "1.1.0":
        legacy_index = _repair_candidate_row(states[-1], unit_id)
        if legacy_index is None or states[-1]["units"][legacy_index]["execution_state"] != "BLOCKED_REVIEW":
            planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
            return _state_result(states[-1], "NO_REPAIR")
        return _publish_state_upgrade(inputs, checked_plan_path, directory, plan, raw_plan, states, raws)

    index = _repair_candidate_row(states[-1], unit_id)
    if index is None:
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        return _state_result(states[-1], "NO_REPAIR")
    row = states[-1]["units"][index]
    if row["execution_state"] == "BLOCKED_REVIEW":
        status, reports, _raws = _authenticated_predecessor(
            inputs, plan, row, plan["units"][index], directory,
        )
        result = classify_repair_findings(
            row["route"], status, reports, run_state_id=states[-1]["run_state_id"],
            prerequisite_ids=row["prerequisite_ids"],
        )
        disposition = result["disposition"]
        rows = copy.deepcopy(states[-1]["units"])
        if disposition == "SAME_SNAPSHOT_REPAIR":
            if len(row["attempts"]) >= 3:
                rows[index]["execution_state"] = "REPAIR_LIMIT_REACHED"
                rows[index]["repair_disposition"] = "REPAIR_LIMIT_REACHED"
                action = f"REPAIR_LIMIT_REACHED:{row['unit_id']}"
            else:
                rows[index] = repair.open_repair_attempt(row)
                action = f"REPAIR_OPENED:{rows[index]['attempt_id']}:{row['unit_id']}"
        elif disposition in {"UPSTREAM_REQUIRED", "STOP_UNSUPPORTED", "REVIEW_INCOMPLETE"}:
            rows[index]["execution_state"] = {
                "UPSTREAM_REQUIRED": "UPSTREAM_REQUIRED",
                "STOP_UNSUPPORTED": "STOP_UNSUPPORTED",
                "REVIEW_INCOMPLETE": "REVIEW_INCOMPLETE_STOP",
            }[disposition]
            rows[index]["repair_disposition"] = disposition
            action = f"{rows[index]['execution_state']}:{row['unit_id']}"
        else:
            _fail("PREDECESSOR_STATUS_INVALID")
        return _persist_next(
            inputs, checked_plan_path, directory, plan, raw_plan, states, raws, rows, action=action,
        )

    paths = _unit_paths(directory, row)
    action, waiting = _expected_action(row, paths)
    if action is None:
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        return _state_result(states[-1], waiting or "NO_ACTION")
    return _execute_boundary(inputs, plan, raw_plan, directory, states, raws, index, action, checked_plan_path)


def _find_orphan(states: list[dict[str, Any]], state_dir: Path) -> tuple[int, str] | None:
    state = states[-1]
    for index, row in enumerate(state["units"]):
        if row["route"] not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
            continue
        paths = _unit_paths(state_dir, row)
        if row["execution_state"] in {"PLANNED", "REPAIR_READY"} and os.path.lexists(paths["prepare"]):
            return index, "PREPARE"
        if row["execution_state"] == "PREPARED":
            if row["route"] == "PHASE6A_PROJECT_CLAIM":
                if any(os.path.lexists(paths["prepare"] / name) for name in ("exercise-bank.json", "chapter-draft-status.json")):
                    return index, "BUILD"
            elif os.path.lexists(paths["unit"]):
                return index, "BUILD"
        if row["execution_state"] == "BUILT_DRAFT" and os.path.lexists(paths["review"]):
            return index, "PREPARE_REVIEW"
        if row["execution_state"] == "AWAITING_REVIEW":
            status = paths["review"] / ("chapter-review-status.json" if row["route"] == "PHASE6A_PROJECT_CLAIM" else "general-review-status.json")
            if os.path.lexists(status):
                return index, "FINALIZE_REVIEW"
    return None


def _recovered_digests(
    state_dir: Path, row: Mapping[str, Any], paths: Mapping[str, Path], phase: str,
    context: Any, plan: Mapping[str, Any],
) -> list[dict[str, str]]:
    facts_context = context.context if hasattr(context, "context") else context
    facts = facts_context.facts if hasattr(facts_context, "facts") else facts_context
    if isinstance(facts, Mapping) and "facts" in facts and "input_digests" not in facts:
        facts = facts["facts"]
    if row["route"] == "PHASE6A_PROJECT_CLAIM":
        prepare_pairs = (("chapter_facts", paths["prepare"] / "chapter-facts.json"), ("writer_packet", paths["prepare"] / "writer-packet.md"))
        if phase == "BUILD":
            pairs = prepare_pairs + (("chapter_markdown", paths["prepare"] / "chapter.md"),
                                      ("exercise_bank", paths["prepare"] / "exercise-bank.json"),
                                      ("chapter_draft_status", paths["prepare"] / "chapter-draft-status.json"))
        elif phase == "FINALIZE_REVIEW":
            pairs = prepare_pairs + (("chapter_markdown", paths["prepare"] / "chapter.md"),
                                     ("exercise_bank", paths["prepare"] / "exercise-bank.json"),
                                     ("chapter_draft_status", paths["prepare"] / "chapter-draft-status.json"),
                                     ("chapter_review_session", paths["review"] / "chapter-review-session.json"),
                                     ("evidence_report", paths["review"] / "evidence-review.json"),
                                     ("beginner_report", paths["review"] / "beginner-review.json"),
                                     ("chapter_review_status", paths["review"] / "chapter-review-status.json"))
        else:
            pairs = prepare_pairs
        candidate_key = facts["selected_unit"]["curriculum_unit_id"]
    else:
        prepare_pairs = (("general_facts", paths["prepare"] / "general-unit-facts.json"), ("writer_packet", paths["prepare"] / "general-unit-writer-packet.md"))
        if phase == "BUILD":
            pairs = prepare_pairs + (("candidate", paths["prepare"] / "general-unit-candidate.json"),
                                     ("general_unit", paths["unit"] / "general-learning-unit.json"),
                                     ("lesson_markdown", paths["unit"] / "general-learning-unit.md"),
                                     ("answer_book_markdown", paths["unit"] / "general-answer-book.md"))
        elif phase == "FINALIZE_REVIEW":
            pairs = prepare_pairs + (("candidate", paths["prepare"] / "general-unit-candidate.json"),
                                     ("general_unit", paths["unit"] / "general-learning-unit.json"),
                                     ("lesson_markdown", paths["unit"] / "general-learning-unit.md"),
                                     ("answer_book_markdown", paths["unit"] / "general-answer-book.md"),
                                     ("general_review_session", paths["review"] / "general-review-session.json"),
                                     ("factuality_report", paths["review"] / "general-factuality-review.json"),
                                     ("beginner_report", paths["review"] / "general-beginner-review.json"),
                                     ("general_review_status", paths["review"] / "general-review-status.json"))
        else:
            pairs = prepare_pairs
        candidate_key = facts.get("selected_unit_id", facts.get("selected_unit", {}).get("id"))
    _unit_identity(row["plan_row"], candidate_key)
    _source_binding(facts, plan, require_inputs="input_digests" in facts)
    if phase == "PREPARE":
        return [_record(state_dir, role, path) for role, path in pairs]
    digest_values = [_record(state_dir, role, path) for role, path in pairs]
    if phase in {"BUILD", "FINALIZE_REVIEW"} and row["route"] == "D2A_GENERAL_LEARNING":
        candidate = next(item for item in digest_values if item["role"] == "candidate")
        artifact, _raw = _facts_from_file(paths["unit"] / "general-learning-unit.json", "general-learning-unit")
        if artifact["candidate_sha256"] != candidate["sha256"]:
            _fail("OUTPUT_INTEGRITY_INVALID")
    existing = {entry["role"]: entry for entry in row["artifact_digests"]}
    additions = []
    for entry in digest_values:
        if entry["role"] in existing:
            if existing[entry["role"]] != entry:
                _fail("OUTPUT_DRIFT")
        else:
            additions.append(entry)
    return additions


def resume_curriculum_run(
    inputs: chapter.ChapterInputPaths, *, plan_path: Path, state_dir: Path,
) -> dict[str, Any]:
    plan, raw_plan, checked_plan_path = _load_plan(inputs, plan_path)
    directory = _external_state_dir(inputs, state_dir, must_exist=True)
    states, raws = load_state_chain(directory, plan, raw_plan)
    _verify_recorded_outputs(directory, states[-1]["units"])
    orphan = _find_orphan(states, directory)
    if orphan is None:
        planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        return _state_result(states[-1], "RESUMED")
    index, phase = orphan
    row = states[-1]["units"][index]
    paths = _unit_paths(directory, row)
    if phase == "PREPARE" and row["route"] == "D2A_GENERAL_LEARNING":
        _fail("ATTEMPT_RECONCILIATION_REQUIRED")
    if phase == "PREPARE":
        plan_context = planning.authenticate_curriculum_run_plan(inputs, plan_path=checked_plan_path)
        plan_row = plan["units"][index]
        facts = chapter._project_claim_facts(
            inputs, row["unit_id"], plan_context.reads, plan_context.prerequisite_graph,
            plan_context.curriculum, plan_context.authenticated,
        )
        if (
            chapter._read_review_file(paths["prepare"] / "chapter-facts.json") != dumps_artifact(facts).encode("utf-8")
            or chapter._read_review_file(paths["prepare"] / "writer-packet.md") != chapter._writer_packet(facts).encode("utf-8")
        ):
            _fail("ATTEMPT_RECONCILIATION_REQUIRED")
        if {path.name for path in paths["prepare"].iterdir()} != {"chapter-facts.json", "writer-packet.md"}:
            _fail("ATTEMPT_RECONCILIATION_REQUIRED")
        _source_binding(facts, plan)
        rows = copy.deepcopy(states[-1]["units"])
        if rows[index]["attempt_id"] is None:
            rows[index]["attempt_id"] = "attempt-0001"
        rows[index]["artifact_digests"].extend((
            _record(directory, "chapter_facts", paths["prepare"] / "chapter-facts.json"),
            _record(directory, "writer_packet", paths["prepare"] / "writer-packet.md"),
        ))
        rows[index]["execution_state"] = "PREPARED"
        _sync_active_attempt(rows[index], states[-1]["schema_version"])
        return _persist_next(inputs, checked_plan_path, directory, plan, raw_plan, states, raws, rows,
                             action=f"RECONCILED_PREPARED:{row['unit_id']}")
    if phase == "BUILD":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            context = chapter.authenticate_chapter_for_review(
                inputs, facts_path=paths["prepare"] / "chapter-facts.json", markdown_path=paths["prepare"] / "chapter.md",
                exercise_bank_path=paths["prepare"] / "exercise-bank.json", draft_status_path=paths["prepare"] / "chapter-draft-status.json",
            )
        else:
            context = d2a2.authenticate_general_unit_for_review(inputs, prepare_dir=paths["prepare"], unit_dir=paths["unit"])
        facts = context.facts if hasattr(context, "facts") else context
        plan_row = plan["units"][index]
        _source_binding(facts, plan)
        rows = copy.deepcopy(states[-1]["units"])
        if rows[index]["attempt_id"] is None:
            rows[index]["attempt_id"] = "attempt-0001"
        rows[index]["artifact_digests"].extend(_recovered_digests(
            directory, {**rows[index], "plan_row": plan_row}, paths, "BUILD", context, plan,
        ))
        rows[index]["execution_state"] = "BUILT_DRAFT"
        _sync_active_attempt(rows[index], states[-1]["schema_version"])
        return _persist_next(inputs, checked_plan_path, directory, plan, raw_plan, states, raws, rows,
                             action=f"RECONCILED_BUILT_DRAFT:{row['unit_id']}")
    if phase == "PREPARE_REVIEW":
        _fail("ATTEMPT_RECONCILIATION_REQUIRED")
    if phase == "FINALIZE_REVIEW":
        if row["route"] == "PHASE6A_PROJECT_CLAIM":
            context = b1.authenticate_chapter_review_package(inputs, chapter_dir=paths["prepare"], review_dir=paths["review"])
            facts = context.context.facts
            status = context.status
        else:
            status = d2a2.authenticate_general_unit_review_status(
                inputs, prepare_dir=paths["prepare"], unit_dir=paths["unit"], review_dir=paths["review"],
            )
            facts = status
        _source_binding(facts, plan, require_inputs="input_digests" in facts)
        rows = copy.deepcopy(states[-1]["units"])
        recovery_context = context if row["route"] == "PHASE6A_PROJECT_CLAIM" else {"facts": facts}
        rows[index]["artifact_digests"].extend(_recovered_digests(
            directory, {**rows[index], "plan_row": plan["units"][index]}, paths,
            "FINALIZE_REVIEW", recovery_context, plan,
        ))
        if status["review_state"] == "REVIEWED_DRAFT":
            rows[index]["execution_state"] = "REVIEWED_DRAFT"
        elif status["review_state"] == "REVIEW_INCOMPLETE" and states[-1]["schema_version"] == "1.1.0":
            rows[index]["execution_state"] = "REVIEW_INCOMPLETE_STOP"
            rows[index]["repair_disposition"] = "REVIEW_INCOMPLETE"
        else:
            rows[index]["execution_state"] = "BLOCKED_REVIEW"
        _sync_active_attempt(rows[index], states[-1]["schema_version"])
        return _persist_next(inputs, checked_plan_path, directory, plan, raw_plan, states, raws, rows,
                             action=f"RECONCILED_{rows[index]['execution_state']}:{row['unit_id']}")
    _fail("ATTEMPT_RECONCILIATION_REQUIRED")


def run_command(
    command: str, inputs: chapter.ChapterInputPaths, *, plan_path: Path, state_dir: Path,
    unit_id: str | None = None,
) -> dict[str, Any]:
    try:
        if command == "start":
            return start_curriculum_run(inputs, plan_path=plan_path, state_dir=state_dir)
        if command == "advance":
            return advance_curriculum_run(inputs, plan_path=plan_path, state_dir=state_dir)
        if command == "resume":
            return resume_curriculum_run(inputs, plan_path=plan_path, state_dir=state_dir)
        if command == "repair":
            return advance_curriculum_repair(inputs, plan_path=plan_path, state_dir=state_dir, unit_id=unit_id)
        _fail("COMMAND_INVALID")
    except CurriculumRunStateError as exc:
        raise CurriculumRunExecutionError(exc.code) from exc
    except (chapter.Phase6ChapterError, Phase6ReviewError, Phase6GeneralLearningError,
            d2a2.GeneralLearningReviewWorkflowError, Phase6GeneralReviewError) as exc:
        raise CurriculumRunExecutionError(getattr(exc, "code", "INPUT_INVALID")) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Advance or resume an authenticated Phase 6 curriculum run.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("start", "advance", "resume", "repair"):
        sub = subparsers.add_parser(command, help=f"{command} an authenticated curriculum run state")
        planning._add_inputs(sub)
        sub.add_argument("--plan", required=True, type=Path)
        sub.add_argument("--state-dir", required=True, type=Path)
        if command == "advance":
            sub.add_argument("--max-actions", type=_positive_action_count, default=1)
            sub.add_argument("--interactive", action="store_true", help="wait on stdin for advance or quit commands")
        if command == "repair":
            sub.add_argument("--unit-id")
    return parser


def _positive_action_count(value: str) -> int:
    try:
        count = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if count <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return count


def _print_state_result(result: Mapping[str, Any]) -> None:
    print(
        f"status={result['run_state']} run_state_id={result['run_state_id']} "
        f"revision={result['state_revision']} action={result.get('action', 'NONE')}",
        flush=True,
    )


def _batch_should_stop(result: Mapping[str, Any]) -> bool:
    action = result.get("action", "NONE")
    return (
        action == "NO_ACTION"
        or action.startswith(("WAITING_", "BLOCKED_"))
        or result["run_state"] == "DRAFTS_REVIEWED"
    )


def _advance_actions(args: argparse.Namespace, inputs: chapter.ChapterInputPaths) -> Mapping[str, Any]:
    result: Mapping[str, Any] = {}
    for _ in range(args.max_actions):
        result = run_command(
            "advance", inputs, plan_path=args.plan, state_dir=args.state_dir,
        )
        _print_state_result(result)
        if _batch_should_stop(result):
            break
    return result


def _advance_batch(args: argparse.Namespace, inputs: chapter.ChapterInputPaths) -> int:
    with chapter._authenticated_input_reuse_scope():
        _advance_actions(args, inputs)
    return 0


def _interactive_is_terminal(result: Mapping[str, Any]) -> bool:
    action = result.get("action", "NONE")
    return (
        action == "NO_ACTION"
        or action.startswith("BLOCKED_")
        or result["run_state"] == "DRAFTS_REVIEWED"
    )


def _advance_interactive(args: argparse.Namespace, inputs: chapter.ChapterInputPaths) -> int:
    with chapter._authenticated_input_reuse_scope():
        while True:
            raw_command = sys.stdin.readline()
            if raw_command == "":
                return 0
            command = raw_command.strip()
            if command == "quit":
                return 0
            if command != "advance":
                print("error=INTERACTIVE_COMMAND_INVALID", flush=True)
                return 2
            result = _advance_actions(args, inputs)
            if _interactive_is_terminal(result):
                return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        inputs = planning._inputs(args)
        if args.command == "advance" and args.interactive:
            return _advance_interactive(args, inputs)
        if args.command == "advance" and args.max_actions > 1:
            return _advance_batch(args, inputs)
        result = run_command(
            args.command, inputs, plan_path=args.plan, state_dir=args.state_dir,
            unit_id=getattr(args, "unit_id", None),
        )
        _print_state_result(result)
        return 0
    except Exception as exc:
        code = getattr(exc, "code", "INPUT_INVALID")
        print(f"error={code}")
        return 2


__all__ = [
    "CurriculumRunExecutionError", "advance_curriculum_repair", "advance_curriculum_run", "resume_curriculum_run",
    "main", "run_command", "start_curriculum_run",
]


if __name__ == "__main__":
    raise SystemExit(main())
