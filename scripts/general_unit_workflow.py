#!/usr/bin/env python3
"""Explicit Phase 6D2A prepare/build CLI; never invokes a model or provider."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

from artifact_contract import dumps_artifact
from phase4_claim_evidence import Phase4ClaimEvidenceError
from phase6_chapter import (
    ChapterInputPaths,
    Phase6ChapterError,
    _absolute,
    _capture_bundle,
    _check_external_output,
    _inside,
    _publish_prepare_files,
    _read_review_file,
    _recheck_inputs,
)
from phase6_general_learning import (
    DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
    Phase6GeneralLearningError,
    build_general_learning_unit,
    general_unit_writer_packet,
    parse_general_unit_candidate,
    project_general_unit_facts,
    render_general_answer_book,
    render_general_learning_unit,
    validate_general_unit_candidate,
)


def _fail(code: str) -> None:
    raise Phase6GeneralLearningError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _capture(inputs: ChapterInputPaths):
    try:
        return _capture_bundle(inputs)
    except Phase6ChapterError as exc:
        _fail(exc.code)


def _check_prepare_output(out_dir: Path, inputs: ChapterInputPaths) -> Path:
    try:
        return _check_external_output(out_dir, inputs, must_exist=False)
    except Phase6ChapterError as exc:
        _fail(exc.code)


def _recheck(authenticated: Any, reads: dict[str, Any]) -> None:
    try:
        authenticated.recheck()
        _recheck_inputs(reads)
    except Phase4ClaimEvidenceError as exc:
        _fail(exc.code)
    except Phase6ChapterError as exc:
        _fail(exc.code)


def prepare_general_unit(
    inputs: ChapterInputPaths,
    *,
    unit_id: str,
    out_dir: Path,
) -> tuple[dict[str, Any], str]:
    """Authenticate one Phase 5 unit and publish facts plus its Writer packet."""
    output = _check_prepare_output(out_dir, inputs)
    authenticated, reads, prerequisite_graph, curriculum = _capture(inputs)
    facts = project_general_unit_facts(
        unit_id, reads, prerequisite_graph, curriculum, authenticated,
    )
    packet = general_unit_writer_packet(facts)
    files = {
        "general-unit-facts.json": dumps_artifact(facts).encode("utf-8"),
        "general-unit-writer-packet.md": packet.encode("utf-8"),
    }
    _recheck(authenticated, reads)
    output = _check_prepare_output(out_dir, inputs)
    try:
        _publish_prepare_files(output, files)
    except Phase6ChapterError as exc:
        _fail(exc.code)
    return facts, packet


def _read_prepare_inputs(
    inputs: ChapterInputPaths,
    facts_path: Path,
    candidate_path: Path,
) -> tuple[Path, bytes, bytes]:
    facts = _absolute(facts_path)
    candidate = _absolute(candidate_path)
    if facts.name != "general-unit-facts.json" or candidate.name != "general-unit-candidate.json":
        _fail("INPUT_INVALID")
    if facts.parent != candidate.parent:
        _fail("INPUT_INVALID")
    try:
        _check_external_output(facts.parent, inputs, must_exist=True)
        entries = list(facts.parent.iterdir())
        if {entry.name for entry in entries} != {
            "general-unit-facts.json", "general-unit-writer-packet.md", "general-unit-candidate.json",
        } or len(entries) != 3:
            _fail("INPUT_INVALID")
        facts_raw = _read_review_file(facts)
        packet_raw = _read_review_file(facts.parent / "general-unit-writer-packet.md")
        candidate_raw = _read_review_file(candidate)
    except Phase6ChapterError as exc:
        _fail(exc.code)
    except OSError:
        _fail("INPUT_INVALID")
    if not packet_raw:
        _fail("INPUT_INVALID")
    return facts.parent, facts_raw, candidate_raw


def _check_build_output(
    out_dir: Path,
    inputs: ChapterInputPaths,
    prepare_dir: Path,
) -> Path:
    try:
        output = _check_external_output(out_dir, inputs, must_exist=False)
    except Phase6ChapterError as exc:
        _fail(exc.code)
    prepare = prepare_dir.resolve(strict=True)
    if _inside(output, prepare) or _inside(prepare, output):
        _fail("OUTPUT_INVALID")
    return output


def build_general_unit(
    inputs: ChapterInputPaths,
    *,
    unit_id: str,
    facts_path: Path,
    candidate_path: Path,
    out_dir: Path,
) -> dict[str, Any]:
    """Reauthenticate, bind, render, and publish one canonical teaching unit."""
    authenticated, reads, prerequisite_graph, curriculum = _capture(inputs)
    facts = project_general_unit_facts(
        unit_id, reads, prerequisite_graph, curriculum, authenticated,
    )
    prepare_dir, facts_raw, candidate_raw = _read_prepare_inputs(inputs, facts_path, candidate_path)
    expected_facts_raw = dumps_artifact(facts).encode("utf-8")
    if facts_raw != expected_facts_raw:
        _fail("PROVENANCE_MISMATCH")
    candidate = parse_general_unit_candidate(candidate_raw)
    validate_general_unit_candidate(candidate, facts)
    lesson_raw = render_general_learning_unit(
        facts, candidate, schema_version=DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
    ).encode("utf-8")
    answer_book_raw = render_general_answer_book(
        facts, candidate, schema_version=DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
    ).encode("utf-8")
    artifact = build_general_learning_unit(
        facts, candidate, candidate_raw, lesson_raw, answer_book_raw,
        schema_version=DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
    )
    files = {
        "general-learning-unit.json": dumps_artifact(artifact).encode("utf-8"),
        "general-learning-unit.md": lesson_raw,
        "general-answer-book.md": answer_book_raw,
    }
    output = _check_build_output(out_dir, inputs, prepare_dir)
    _recheck(authenticated, reads)
    if (
        _read_review_file(Path(facts_path)) != facts_raw
        or _read_review_file(Path(candidate_path)) != candidate_raw
    ):
        _fail("INPUT_CHANGED")
    output = _check_build_output(out_dir, inputs, prepare_dir)
    try:
        _publish_prepare_files(output, files)
    except Phase6ChapterError as exc:
        _fail(exc.code)
    return artifact


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase4c-package", required=True, type=Path)
    parser.add_argument("--claim-candidates", required=True, type=Path)
    parser.add_argument("--claim-evidence", required=True, type=Path)
    parser.add_argument("--claim-evidence-graph", required=True, type=Path)
    parser.add_argument("--prerequisite-candidates", required=True, type=Path)
    parser.add_argument("--prerequisite-graph", required=True, type=Path)
    parser.add_argument("--curriculum-candidates", required=True, type=Path)
    parser.add_argument("--curriculum", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and build one general-learning Phase 5 teaching unit.")
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="authenticate one unit and publish facts plus Writer packet")
    _add_inputs(prepare)
    build = commands.add_parser("build", help="build a bound candidate into a lesson and answer book")
    _add_inputs(build)
    build.add_argument("--facts", required=True, type=Path)
    build.add_argument("--candidate", required=True, type=Path)
    return parser


def _inputs(args: argparse.Namespace) -> ChapterInputPaths:
    return ChapterInputPaths(
        phase4c_package=args.phase4c_package,
        claim_candidates_path=args.claim_candidates,
        claim_evidence_path=args.claim_evidence,
        claim_evidence_graph_path=args.claim_evidence_graph,
        prerequisite_candidates_path=args.prerequisite_candidates,
        prerequisite_graph_path=args.prerequisite_graph,
        curriculum_candidates_path=args.curriculum_candidates,
        curriculum_path=args.curriculum,
        run_dir=args.run_dir,
        root=args.root,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            facts, _packet = prepare_general_unit(
                _inputs(args), unit_id=args.unit_id, out_dir=args.out_dir,
            )
            unit = facts["selected_unit"]
            print(
                f"status=PREPARED unit_id={unit['id']} origin={unit['origin']} scope={unit['scope']} "
                f"source_status={facts['source_status']} unknown_files={facts['unknown_files']}"
            )
            return 0
        artifact = build_general_unit(
            _inputs(args), unit_id=args.unit_id, facts_path=args.facts,
            candidate_path=args.candidate, out_dir=args.out_dir,
        )
        print(
            f"status=BUILT unit_id={artifact['selected_unit']['id']} overall_status={artifact['overall_status']} "
            f"candidate_sha256={artifact['candidate_sha256']} "
            f"lesson_sha256={artifact['lesson_markdown_sha256']} "
            f"answer_book_sha256={artifact['answer_book_markdown_sha256']}"
        )
        return 0
    except Phase6GeneralLearningError as exc:
        print(f"error={exc.code}")
        return 2
    except Exception:
        print("error=INPUT_INVALID")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
