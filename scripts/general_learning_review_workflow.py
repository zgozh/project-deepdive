#!/usr/bin/env python3
"""Prepare and finalize opt-in, read-only Phase 6D2A2 review sidecars."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, _strict_json_loads, dumps_artifact, validate_artifact
from phase4_claim_evidence import Phase4ClaimEvidenceError
from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    _absolute,
    _capture_bundle,
    _check_external_output,
    _inside,
    _is_link,
    _publish_new_file,
    _publish_prepare_files,
    _read_review_file,
    _recheck_inputs,
)
from phase6_general_learning import (
    GENERAL_LEARNING_UNIT_VERSIONS,
    Phase6GeneralLearningError,
    build_general_learning_unit,
    general_unit_writer_packet,
    parse_general_unit_candidate,
    project_general_unit_facts,
    render_general_answer_book,
    render_general_learning_unit,
    validate_general_unit_candidate,
)
from phase6_general_review import (
    Phase6GeneralReviewError,
    ReviewMaterial,
    aggregate_general_review_status,
    build_general_review_material,
    validate_general_beginner_report,
    validate_general_factuality_report,
)


_PREPARE_FILES = {
    "general-review-session.json",
    "general-factuality-review-packet.md",
    "general-beginner-answer-review-packet.md",
    "general-factuality-review-template.json",
    "general-beginner-answer-review-template.json",
}
_REPORT_FILES = {"general-factuality-review.json", "general-beginner-review.json"}
_D2A_PREPARE_FILES = {"general-unit-facts.json", "general-unit-writer-packet.md", "general-unit-candidate.json"}
_D2A_UNIT_FILES = {"general-learning-unit.json", "general-learning-unit.md", "general-answer-book.md"}


class GeneralLearningReviewWorkflowError(ValueError):
    """A fixed, redacted D2A2 workflow failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise GeneralLearningReviewWorkflowError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _inside_or_same(path: Path, other: Path) -> bool:
    return path == other or _inside(path, other) or _inside(other, path)


def _ensure_disjoint(path: Path, *others: Path) -> None:
    if any(_inside_or_same(path, _absolute(other)) for other in others):
        _fail("OUTPUT_INVALID")


def _capture(inputs: ChapterInputPaths):
    try:
        return _capture_bundle(inputs)
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc


def _read_dir(path: Path, expected: set[str], inputs: ChapterInputPaths) -> tuple[Path, dict[str, bytes]]:
    try:
        directory = _check_external_output(path, inputs, must_exist=True)
        if _is_link(directory):
            _fail("INPUT_INVALID")
        entries = list(directory.iterdir())
        if len(entries) != len(expected) or {item.name for item in entries} != expected:
            _fail("INPUT_INVALID")
        raw = {name: _read_review_file(directory / name) for name in sorted(expected)}
        return directory, raw
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    except OSError as exc:
        raise GeneralLearningReviewWorkflowError("INPUT_INVALID") from exc


def _rebuild_candidate(facts: Mapping[str, Any], candidate_raw: bytes, *, schema_version: str):
    try:
        candidate = parse_general_unit_candidate(candidate_raw)
        validate_general_unit_candidate(candidate, facts)
        lesson_raw = render_general_learning_unit(facts, candidate, schema_version=schema_version).encode("utf-8")
        answer_book_raw = render_general_answer_book(facts, candidate, schema_version=schema_version).encode("utf-8")
        artifact = build_general_learning_unit(
            facts, candidate, candidate_raw, lesson_raw, answer_book_raw, schema_version=schema_version,
        )
        return candidate, lesson_raw, answer_book_raw, artifact, dumps_artifact(artifact).encode("utf-8")
    except Phase6GeneralLearningError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    except ArtifactValidationError as exc:
        raise GeneralLearningReviewWorkflowError("INPUT_INVALID") from exc


@dataclass(frozen=True)
class GeneralUnitReviewContext:
    inputs: ChapterInputPaths
    prepare_dir: Path
    unit_dir: Path
    authenticated: Any
    reads: dict[str, Any]
    facts: dict[str, Any]
    facts_raw: bytes
    writer_packet_raw: bytes
    candidate: dict[str, Any]
    candidate_raw: bytes
    artifact: dict[str, Any]
    artifact_raw: bytes
    lesson_raw: bytes
    answer_book_raw: bytes
    prepare_raw: dict[str, bytes]
    unit_raw: dict[str, bytes]

    def recheck(self) -> None:
        try:
            self.authenticated.recheck()
            _recheck_inputs(self.reads)
        except (Phase4ClaimEvidenceError, Phase6ChapterError) as exc:
            raise GeneralLearningReviewWorkflowError(exc.code) from exc
        _directory, current_prepare = _read_dir(self.prepare_dir, _D2A_PREPARE_FILES, self.inputs)
        _directory, current_unit = _read_dir(self.unit_dir, _D2A_UNIT_FILES, self.inputs)
        if current_prepare != self.prepare_raw or current_unit != self.unit_raw:
            _fail("INPUT_CHANGED")


def authenticate_general_unit_for_review(
    inputs: ChapterInputPaths, *, prepare_dir: Path, unit_dir: Path,
) -> GeneralUnitReviewContext:
    """Replay Phase 4/5 once and reconstruct exact immutable D2A outputs."""
    prepare_path, prepare_raw = _read_dir(prepare_dir, _D2A_PREPARE_FILES, inputs)
    unit_path, unit_raw = _read_dir(unit_dir, _D2A_UNIT_FILES, inputs)
    _ensure_disjoint(prepare_path, unit_path)
    try:
        prepared_facts = _strict_json_loads(prepare_raw["general-unit-facts.json"].decode("utf-8"))
        validate_artifact(prepared_facts)
    except (ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise GeneralLearningReviewWorkflowError("INPUT_INVALID") from exc
    if prepared_facts.get("artifact_kind") != "general-unit-facts":
        _fail("INPUT_INVALID")
    try:
        stored_unit = _strict_json_loads(unit_raw["general-learning-unit.json"].decode("utf-8"))
        validate_artifact(stored_unit)
    except (ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise GeneralLearningReviewWorkflowError("UNIT_BINDING_INVALID") from exc
    if (
        stored_unit.get("artifact_kind") != "general-learning-unit"
        or stored_unit.get("schema_version") not in GENERAL_LEARNING_UNIT_VERSIONS
    ):
        _fail("UNIT_BINDING_INVALID")
    authenticated, reads, prerequisite_graph, curriculum = _capture(inputs)
    try:
        facts = project_general_unit_facts(
            prepared_facts["selected_unit"]["id"], reads, prerequisite_graph, curriculum, authenticated,
        )
    except Phase6GeneralLearningError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    facts_raw = dumps_artifact(facts).encode("utf-8")
    writer_packet_raw = general_unit_writer_packet(facts).encode("utf-8")
    if (
        prepare_raw["general-unit-facts.json"] != facts_raw
        or prepare_raw["general-unit-writer-packet.md"] != writer_packet_raw
    ):
        _fail("PROVENANCE_MISMATCH")
    candidate_raw = prepare_raw["general-unit-candidate.json"]
    candidate, lesson_raw, answer_book_raw, artifact, artifact_raw = _rebuild_candidate(
        facts, candidate_raw, schema_version=stored_unit["schema_version"],
    )
    expected_unit = {
        "general-learning-unit.json": artifact_raw,
        "general-learning-unit.md": lesson_raw,
        "general-answer-book.md": answer_book_raw,
    }
    if unit_raw != expected_unit:
        _fail("UNIT_BINDING_INVALID")
    if (
        artifact["lesson_status"] != "DRAFT" or artifact["answer_book_status"] != "DRAFT"
        or artifact["overall_status"] != "PARTIAL" or artifact["epistemic_status"] != "UNVERIFIED_TEACHING"
        or any(artifact[key] != "NOT_RUN" for key in ("general_fact_review", "beginner_review", "answer_book_review"))
        or artifact["source_status"] not in {"PASS", "PARTIAL"}
        or artifact["unknown_files"] != facts["unknown_files"]
    ):
        _fail("UNIT_BINDING_INVALID")
    context = GeneralUnitReviewContext(
        inputs, prepare_path, unit_path, authenticated, reads, facts, facts_raw, writer_packet_raw,
        candidate, candidate_raw, artifact, artifact_raw, lesson_raw, answer_book_raw, prepare_raw, unit_raw,
    )
    context.recheck()
    return context


def _check_review_dir(path: Path, inputs: ChapterInputPaths, *, must_exist: bool, prepare_dir: Path, unit_dir: Path) -> Path:
    try:
        output = _check_external_output(path, inputs, must_exist=must_exist)
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    _ensure_disjoint(output, prepare_dir, unit_dir)
    return output


def _material(context: GeneralUnitReviewContext) -> tuple[ReviewMaterial, bytes]:
    try:
        material = build_general_review_material(context)
        session_raw = dumps_artifact(material.session).encode("utf-8")
        return material, session_raw
    except (Phase6GeneralReviewError, ArtifactValidationError) as exc:
        code = getattr(exc, "code", "OUTPUT_INVALID")
        raise GeneralLearningReviewWorkflowError(code) from exc


def prepare_general_unit_review(
    inputs: ChapterInputPaths,
    *,
    prepare_dir: Path,
    unit_dir: Path,
    review_dir: Path,
) -> dict[str, Any]:
    """Publish exact role-separated packets and templates; invoke no reviewer."""
    output = _check_review_dir(review_dir, inputs, must_exist=False, prepare_dir=prepare_dir, unit_dir=unit_dir)
    context = authenticate_general_unit_for_review(inputs, prepare_dir=prepare_dir, unit_dir=unit_dir)
    material, session_raw = _material(context)
    files = {
        "general-review-session.json": session_raw,
        "general-factuality-review-packet.md": material.factuality_packet_raw,
        "general-beginner-answer-review-packet.md": material.beginner_packet_raw,
        "general-factuality-review-template.json": material.factuality_template_raw,
        "general-beginner-answer-review-template.json": material.beginner_template_raw,
    }
    context.recheck()
    output = _check_review_dir(output, inputs, must_exist=False, prepare_dir=prepare_dir, unit_dir=unit_dir)
    try:
        _publish_prepare_files(output, files)
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    return {
        "command": "prepare", "review_session_id": material.session["review_session_id"],
        "factuality_packet_sha256": material.session["factuality_packet_sha256"],
        "beginner_packet_sha256": material.session["beginner_packet_sha256"],
    }


def _read_review_dir(review_dir: Path, inputs: ChapterInputPaths, prepare_dir: Path, unit_dir: Path) -> tuple[Path, dict[str, bytes]]:
    output = _check_review_dir(review_dir, inputs, must_exist=True, prepare_dir=prepare_dir, unit_dir=unit_dir)
    try:
        entries = list(output.iterdir())
        names = {item.name for item in entries}
        if "general-review-status.json" in names or not _PREPARE_FILES.issubset(names) or names - _PREPARE_FILES - _REPORT_FILES:
            _fail("REVIEW_PACKAGE_INVALID")
        raw = {name: _read_review_file(output / name) for name in sorted(names)}
        return output, raw
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    except OSError as exc:
        raise GeneralLearningReviewWorkflowError("REVIEW_PACKAGE_INVALID") from exc


def _read_finalized_review_dir(
    review_dir: Path, inputs: ChapterInputPaths, prepare_dir: Path, unit_dir: Path,
) -> tuple[Path, dict[str, bytes]]:
    """Read the exact finalized package for the non-publishing recovery authenticator."""
    output = _check_review_dir(review_dir, inputs, must_exist=True, prepare_dir=prepare_dir, unit_dir=unit_dir)
    required = _PREPARE_FILES | {"general-review-status.json"}
    allowed = required | _REPORT_FILES
    try:
        entries = list(output.iterdir())
        names = {entry.name for entry in entries}
        if len(entries) != len(names) or not required.issubset(names) or names - allowed:
            _fail("REVIEW_PACKAGE_INVALID")
        return output, {name: _read_review_file(output / name) for name in sorted(names)}
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    except OSError as exc:
        raise GeneralLearningReviewWorkflowError("REVIEW_PACKAGE_INVALID") from exc


def _read_report(path: Path, kind: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = _read_review_file(path)
        report = _strict_json_loads(raw.decode("utf-8"))
        validate_artifact(report)
    except (Phase6ChapterError, ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise GeneralLearningReviewWorkflowError("REPORT_INVALID") from exc
    if not isinstance(report, dict) or report.get("artifact_kind") != kind:
        _fail("REPORT_INVALID")
    return report, raw


def _validated_status(
    context: GeneralUnitReviewContext, output: Path, raw_by_name: Mapping[str, bytes],
) -> tuple[dict[str, Any], bytes]:
    """Share exact session/report validation between finalization and read-only recovery."""
    material, session_raw = _material(context)
    expected = {
        "general-review-session.json": session_raw,
        "general-factuality-review-packet.md": material.factuality_packet_raw,
        "general-beginner-answer-review-packet.md": material.beginner_packet_raw,
        "general-factuality-review-template.json": material.factuality_template_raw,
        "general-beginner-answer-review-template.json": material.beginner_template_raw,
    }
    if any(raw_by_name.get(name) != payload for name, payload in expected.items()):
        _fail("SESSION_BINDING_INVALID")
    fact_report = fact_raw = beginner_report = beginner_raw = None
    if "general-factuality-review.json" in raw_by_name:
        fact_report, fact_raw = _read_report(output / "general-factuality-review.json", "general-factuality-review")
        validate_general_factuality_report(
            fact_report, material.session, session_raw, material.factuality_packet_raw, material.occurrences,
        )
    if "general-beginner-review.json" in raw_by_name:
        beginner_report, beginner_raw = _read_report(output / "general-beginner-review.json", "general-beginner-review")
        validate_general_beginner_report(
            beginner_report, material.session, session_raw, material.beginner_packet_raw, material.scopes,
        )
    try:
        status = aggregate_general_review_status(
            context, material.session, (fact_report, fact_raw), (beginner_report, beginner_raw), material.occurrences,
        )
        return status, dumps_artifact(status).encode("utf-8")
    except Phase6GeneralReviewError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc


def finalize_general_unit_review(
    inputs: ChapterInputPaths,
    *,
    prepare_dir: Path,
    unit_dir: Path,
    review_dir: Path,
    out_status: Path,
) -> dict[str, Any]:
    """Replay D2A and atomically publish only the non-promoting status sidecar."""
    context = authenticate_general_unit_for_review(inputs, prepare_dir=prepare_dir, unit_dir=unit_dir)
    output, raw_by_name = _read_review_dir(review_dir, inputs, context.prepare_dir, context.unit_dir)
    status_path = _absolute(out_status)
    if status_path != output / "general-review-status.json":
        _fail("OUTPUT_INVALID")
    if os.path.lexists(status_path):
        _fail("OUTPUT_EXISTS")
    status, status_raw = _validated_status(context, output, raw_by_name)
    context.recheck()
    _current, after = _read_review_dir(output, inputs, context.prepare_dir, context.unit_dir)
    if after != raw_by_name or os.path.lexists(status_path):
        _fail("INPUT_CHANGED")
    try:
        _publish_new_file(status_path, status_raw)
    except Phase6ChapterError as exc:
        raise GeneralLearningReviewWorkflowError(exc.code) from exc
    return status


def authenticate_general_unit_review_status(
    inputs: ChapterInputPaths,
    *,
    prepare_dir: Path,
    unit_dir: Path,
    review_dir: Path,
) -> dict[str, Any]:
    """Replay one finalized D2A2 package and return its exact non-promoting status."""
    context = authenticate_general_unit_for_review(inputs, prepare_dir=prepare_dir, unit_dir=unit_dir)
    output, raw_by_name = _read_finalized_review_dir(review_dir, inputs, context.prepare_dir, context.unit_dir)
    status, status_raw = _validated_status(context, output, raw_by_name)
    stored_raw = raw_by_name["general-review-status.json"]
    try:
        stored = _strict_json_loads(stored_raw.decode("utf-8"))
        validate_artifact(stored)
    except (ArtifactValidationError, UnicodeError, ValueError, TypeError) as exc:
        raise GeneralLearningReviewWorkflowError("REVIEW_STATUS_INVALID") from exc
    if stored_raw != status_raw or stored != status:
        _fail("REVIEW_STATUS_INVALID")
    context.recheck()
    _current, after = _read_finalized_review_dir(output, inputs, context.prepare_dir, context.unit_dir)
    if after != raw_by_name:
        _fail("INPUT_CHANGED")
    return status


def _inputs(args: argparse.Namespace) -> ChapterInputPaths:
    return ChapterInputPaths(
        phase4c_package=Path(args.phase4c_package), claim_candidates_path=Path(args.claim_candidates),
        claim_evidence_path=Path(args.claim_evidence), claim_evidence_graph_path=Path(args.claim_evidence_graph),
        prerequisite_candidates_path=Path(args.prerequisite_candidates), prerequisite_graph_path=Path(args.prerequisite_graph),
        curriculum_candidates_path=Path(args.curriculum_candidates), curriculum_path=Path(args.curriculum),
        run_dir=Path(args.run_dir), root=Path(args.root),
    )


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    for option in (
        "phase4c-package", "claim-candidates", "claim-evidence", "claim-evidence-graph",
        "prerequisite-candidates", "prerequisite-graph", "curriculum-candidates", "curriculum",
        "run-dir", "root",
    ):
        parser.add_argument("--" + option, required=True, type=Path)
    parser.add_argument("--prepare-dir", required=True, type=Path)
    parser.add_argument("--unit-dir", required=True, type=Path)
    parser.add_argument("--review-dir", required=True, type=Path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="replay one D2A unit and publish independent review packets")
    _add_inputs(prepare)
    finalize = commands.add_parser("finalize", help="validate any supplied reports and publish review status")
    _add_inputs(finalize)
    finalize.add_argument("--out-status", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        inputs = _inputs(args)
        if args.command == "prepare":
            result = prepare_general_unit_review(
                inputs, prepare_dir=args.prepare_dir, unit_dir=args.unit_dir, review_dir=args.review_dir,
            )
            print(f"status=PREPARED review_session_id={result['review_session_id']}")
        else:
            result = finalize_general_unit_review(
                inputs, prepare_dir=args.prepare_dir, unit_dir=args.unit_dir,
                review_dir=args.review_dir, out_status=args.out_status,
            )
            print(
                f"status={result['review_state']} source_status={result['source_status']} "
                f"unknown_files={result['unknown_files']} supported_fact_occurrences={result['supported_fact_occurrences']}"
            )
        return 0
    except (GeneralLearningReviewWorkflowError, Phase6GeneralReviewError) as exc:
        print(f"error={getattr(exc, 'code', 'INPUT_INVALID')}")
        return 2
    except Exception:
        print("error=INPUT_INVALID")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
