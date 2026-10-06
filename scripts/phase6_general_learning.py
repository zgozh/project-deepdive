#!/usr/bin/env python3
"""Pure Phase 6D2A general-unit selection, binding, and rendering."""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from artifact_contract import (
    ArtifactValidationError,
    _strict_json_loads,
    dumps_artifact,
    validate_artifact,
)
from phase6_chapter import _authenticated_input_digest_records


class Phase6GeneralLearningError(ValueError):
    """A fixed, redacted teaching-unit input or binding failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_EMPTY_REF_KEYS = ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")
_PRIMER_FIELDS = ("primer_concept_key", "prerequisite_node_id", "concept_epistemic_status")
GENERAL_LEARNING_UNIT_VERSIONS = ("1.0.0", "1.1.0")
DEFAULT_GENERAL_LEARNING_UNIT_VERSION = "1.1.0"


def _fail(code: str) -> None:
    raise Phase6GeneralLearningError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_facts_bytes(facts: Mapping[str, Any]) -> bytes:
    return dumps_artifact(facts).encode("utf-8")


def _has_empty_project_refs(unit: Mapping[str, Any]) -> bool:
    refs = unit.get("project_refs")
    return (
        isinstance(refs, Mapping)
        and set(refs) == set(_EMPTY_REF_KEYS)
        and all(isinstance(refs[key], list) and not refs[key] for key in _EMPTY_REF_KEYS)
    )


def select_general_learning_unit(
    curriculum: Mapping[str, Any], unit_id: str,
) -> dict[str, Any]:
    """Select one exact Phase 5 unit accepted by the separate teaching path."""
    units = curriculum.get("units")
    if not isinstance(units, list):
        _fail("INPUT_INVALID")
    matches = [unit for unit in units if isinstance(unit, Mapping) and unit.get("id") == unit_id]
    if len(matches) != 1:
        _fail("UNIT_NOT_FOUND")
    unit = matches[0]
    if not _has_empty_project_refs(unit):
        _fail("PROJECT_REFERENCES_PRESENT")
    origin = unit.get("origin")
    scope = unit.get("scope")
    if origin == "CANDIDATE":
        if scope != "GENERAL_LEARNING" or not isinstance(unit.get("unit_key"), str) or not unit["unit_key"]:
            _fail("UNIT_ORIGIN_SCOPE_INVALID")
        if any(field in unit for field in _PRIMER_FIELDS):
            _fail("UNIT_METADATA_INVALID")
    elif origin == "PREREQUISITE_PRIMER":
        if scope not in {"GENERAL_LEARNING", "PROJECT_SPECIFIC"}:
            _fail("UNIT_ORIGIN_SCOPE_INVALID")
        if "unit_key" in unit or any(not isinstance(unit.get(field), str) or not unit[field] for field in _PRIMER_FIELDS):
            _fail("UNIT_METADATA_INVALID")
    else:
        _fail("UNIT_ORIGIN_SCOPE_INVALID")
    return copy.deepcopy(dict(unit))


def project_general_unit_facts(
    unit_id: str,
    phase5_reads: Mapping[str, Any],
    prerequisite_graph: Mapping[str, Any],
    curriculum: Mapping[str, Any],
    authenticated: Any,
) -> dict[str, Any]:
    """Project authenticated Phase 5 teaching identity without project claims."""
    del prerequisite_graph  # _capture_bundle has already reconstructed and cross-checked it.
    unit = select_general_learning_unit(curriculum, unit_id)
    try:
        digest_records = _authenticated_input_digest_records(phase5_reads, authenticated)
        source_metadata = copy.deepcopy(curriculum["source_metadata"])
        facts = {
            "artifact_kind": "general-unit-facts",
            "schema_version": "1.0.0",
            "repository_revision": curriculum["repository_revision"],
            "generated_at": curriculum["generated_at"],
            "snapshot_kind": curriculum["snapshot_kind"],
            "source_metadata": source_metadata,
            "source_status": curriculum["source_status"],
            "source_run_manifest_sha256": curriculum["source_run_manifest_sha256"],
            "unknown_files": source_metadata["unknown_files"],
            "input_digests": digest_records,
            "selected_unit": unit,
        }
        validate_artifact(facts)
        if len(digest_records) != 10 or facts["unknown_files"] != source_metadata["unknown_files"]:
            _fail("INPUT_INVALID")
        return facts
    except Phase6GeneralLearningError:
        raise
    except (ArtifactValidationError, KeyError, TypeError, ValueError):
        _fail("INPUT_INVALID")


def parse_general_unit_candidate(candidate_raw: bytes) -> dict[str, Any]:
    """Parse strict JSON, reject duplicate keys, and validate the candidate schema."""
    try:
        candidate = _strict_json_loads(candidate_raw.decode("utf-8"))
        if not isinstance(candidate, dict):
            _fail("CANDIDATE_INVALID")
        validate_artifact(candidate)
        return candidate
    except Phase6GeneralLearningError:
        raise
    except (ArtifactValidationError, UnicodeDecodeError, TypeError, ValueError):
        _fail("CANDIDATE_INVALID")


def validate_general_unit_candidate(
    candidate: Mapping[str, Any], facts: Mapping[str, Any],
) -> None:
    """Check candidate-to-facts identity and exact prerequisite/concept coverage."""
    try:
        validate_artifact(dict(candidate))
        unit = facts["selected_unit"]
        facts_sha256 = _sha256(_canonical_facts_bytes(facts))
        if (
            candidate.get("curriculum_unit_id") != unit.get("id")
            or candidate.get("facts_sha256") != facts_sha256
            or candidate.get("origin") != unit.get("origin")
            or candidate.get("scope") != unit.get("scope")
        ):
            _fail("PROVENANCE_MISMATCH")
        if unit["origin"] == "CANDIDATE":
            expected_fields = {"unit_key": unit["unit_key"]}
        else:
            expected_fields = {field: unit[field] for field in _PRIMER_FIELDS}
        actual_fields = {
            field: candidate[field]
            for field in ("unit_key", *_PRIMER_FIELDS)
            if field in candidate
        }
        if actual_fields != expected_fields:
            _fail("UNIT_METADATA_INVALID")
        lesson = candidate["lesson"]
        for field, expected in (
            ("prerequisite_explanations", unit["requires_concept_keys"]),
            ("concept_explanations", unit["introduces_concept_keys"]),
        ):
            actual = [entry["concept_key"] for entry in lesson[field]]
            if len(actual) != len(set(actual)) or set(actual) != set(expected):
                _fail("CONCEPT_COVERAGE_INVALID")
    except Phase6GeneralLearningError:
        raise
    except (ArtifactValidationError, KeyError, TypeError, ValueError):
        _fail("CANDIDATE_INVALID")


def _concept_entries(candidate: Mapping[str, Any], unit: Mapping[str, Any], field: str, concept_field: str) -> list[Mapping[str, str]]:
    by_key = {entry["concept_key"]: entry for entry in candidate["lesson"][field]}
    return [by_key[key] for key in unit[concept_field]]


def _render_general_learning_unit_v10(
    facts: Mapping[str, Any], candidate: Mapping[str, Any],
) -> str:
    """Render a standalone question-only lesson from the bound candidate."""
    validate_general_unit_candidate(candidate, facts)
    unit = facts["selected_unit"]
    lesson = candidate["lesson"]
    lines = [
        f"# {unit['title']}",
        "",
        "> DRAFT — UNVERIFIED_TEACHING. This is general teaching content; target-project behavior is not asserted.",
        "",
        f"- Curriculum origin: {unit['origin']}",
        f"- Curriculum scope: {unit['scope']}",
        f"- Prerequisite unit IDs: {', '.join(unit['prerequisite_ids']) or 'none'}",
        f"- Required concept keys: {', '.join(unit['requires_concept_keys']) or 'none'}",
        f"- Introduced concept keys: {', '.join(unit['introduces_concept_keys']) or 'none'}",
        "",
        "## Intuition",
        "",
        lesson["intuition"],
        "",
        "## Prerequisite concepts",
        "",
    ]
    for entry in _concept_entries(candidate, unit, "prerequisite_explanations", "requires_concept_keys"):
        lines.extend([f"### {entry['concept_key']}", "", entry["explanation"], ""])
    if not unit["requires_concept_keys"]:
        lines.extend(["No prerequisite concepts are listed for this unit.", ""])
    lines.extend(["## Concepts in this unit", ""])
    for entry in _concept_entries(candidate, unit, "concept_explanations", "introduces_concept_keys"):
        lines.extend([f"### {entry['concept_key']}", "", entry["explanation"], ""])
    if not unit["introduces_concept_keys"]:
        lines.extend(["No concept keys are listed as introduced by this unit.", ""])
    lines.extend([
        "## Worked example",
        "",
        lesson["worked_example"],
        "",
        "## Common misconception",
        "",
        lesson["common_misconception"],
        "",
        "## Check yourself",
        "",
        lesson["learning_check"]["question"],
        "",
    ])
    return "\n".join(lines)


def render_general_learning_unit(
    facts: Mapping[str, Any], candidate: Mapping[str, Any], *, schema_version: str = DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
) -> str:
    """Render a question-only lesson using the exact declared artifact version."""
    if schema_version == "1.0.0":
        return _render_general_learning_unit_v10(facts, candidate)
    if schema_version != "1.1.0":
        _fail("OUTPUT_INVALID")
    validate_general_unit_candidate(candidate, facts)
    unit = facts["selected_unit"]
    lesson = candidate["lesson"]
    lines = [
        f"# {unit['title']}",
        "",
        "> Draft teaching material - not independently reviewed or verified.",
        "",
        "## Intuition",
        "",
        lesson["intuition"],
        "",
    ]
    prerequisite_entries = _concept_entries(candidate, unit, "prerequisite_explanations", "requires_concept_keys")
    if prerequisite_entries:
        lines.extend(["## Prerequisite ideas", ""])
        for index, entry in enumerate(prerequisite_entries, start=1):
            lines.extend([f"### Prerequisite idea {index}", "", entry["explanation"], ""])
    concept_entries = _concept_entries(candidate, unit, "concept_explanations", "introduces_concept_keys")
    if concept_entries:
        lines.extend(["## New concepts", ""])
        for index, entry in enumerate(concept_entries, start=1):
            lines.extend([f"### New concept {index}", "", entry["explanation"], ""])
    lines.extend([
        "## Worked example",
        "",
        lesson["worked_example"],
        "",
        "## Common misconception",
        "",
        lesson["common_misconception"],
        "",
        "## Check yourself",
        "",
        lesson["learning_check"]["question"],
        "",
    ])
    return "\n".join(lines)


def _render_general_answer_book_v10(
    facts: Mapping[str, Any], candidate: Mapping[str, Any],
) -> str:
    """Render the separate prompt-plus-answer booklet from the same candidate."""
    validate_general_unit_candidate(candidate, facts)
    unit = facts["selected_unit"]
    check = candidate["lesson"]["learning_check"]
    lines = [
        f"# Answer Book: {unit['title']}",
        "",
        "> DRAFT — UNVERIFIED_TEACHING. Reference answers and hints have not been fact-checked or independently reviewed.",
        "",
        f"- Curriculum origin: {unit['origin']}",
        f"- Curriculum scope: {unit['scope']}",
        "",
        "## Question",
        "",
        check["question"],
        "",
        "## Worked reference answer",
        "",
        check["reference_answer"],
        "",
        "## Progressive hints",
        "",
    ]
    lines.extend(f"{index}. {hint}" for index, hint in enumerate(check["hints"], start=1))
    lines.extend(["", "## Qualitative rubric", ""])
    lines.extend(f"- {item}" for item in check["rubric"])
    lines.append("")
    return "\n".join(lines)


def render_general_answer_book(
    facts: Mapping[str, Any], candidate: Mapping[str, Any], *, schema_version: str = DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
) -> str:
    """Render the matching question, reference answer, hints, and rubric booklet."""
    if schema_version == "1.0.0":
        return _render_general_answer_book_v10(facts, candidate)
    if schema_version != "1.1.0":
        _fail("OUTPUT_INVALID")
    validate_general_unit_candidate(candidate, facts)
    unit = facts["selected_unit"]
    check = candidate["lesson"]["learning_check"]
    lines = [
        f"# Answer Book: {unit['title']}",
        "",
        "> Draft teaching material - not independently reviewed or verified.",
        "",
        "## Question",
        "",
        check["question"],
        "",
        "## Answer",
        "",
        check["reference_answer"],
        "",
        "## Hints",
        "",
    ]
    lines.extend(f"- {hint}" for hint in check["hints"])
    lines.extend(["", "## Rubric", ""])
    lines.extend(f"- {item}" for item in check["rubric"])
    lines.append("")
    return "\n".join(lines)


def build_general_learning_unit(
    facts: Mapping[str, Any],
    candidate: Mapping[str, Any],
    candidate_raw: bytes,
    lesson_markdown_raw: bytes,
    answer_book_markdown_raw: bytes,
    *,
    schema_version: str = DEFAULT_GENERAL_LEARNING_UNIT_VERSION,
) -> dict[str, Any]:
    """Construct the versioned canonical artifact with exact raw-byte digests."""
    parsed = parse_general_unit_candidate(candidate_raw)
    if parsed != dict(candidate):
        _fail("CANDIDATE_INVALID")
    validate_general_unit_candidate(parsed, facts)
    if schema_version not in GENERAL_LEARNING_UNIT_VERSIONS:
        _fail("OUTPUT_INVALID")
    expected_lesson = render_general_learning_unit(facts, parsed, schema_version=schema_version).encode("utf-8")
    expected_answer_book = render_general_answer_book(facts, parsed, schema_version=schema_version).encode("utf-8")
    if lesson_markdown_raw != expected_lesson or answer_book_markdown_raw != expected_answer_book:
        _fail("OUTPUT_INVALID")
    artifact = {
        "artifact_kind": "general-learning-unit",
        "schema_version": schema_version,
        "repository_revision": facts["repository_revision"],
        "generated_at": facts["generated_at"],
        "snapshot_kind": facts["snapshot_kind"],
        "source_metadata": copy.deepcopy(facts["source_metadata"]),
        "source_status": facts["source_status"],
        "source_run_manifest_sha256": facts["source_run_manifest_sha256"],
        "unknown_files": facts["unknown_files"],
        "input_digests": copy.deepcopy(facts["input_digests"]),
        "selected_unit": copy.deepcopy(facts["selected_unit"]),
        "candidate": copy.deepcopy(dict(parsed)),
        "prepared_facts_sha256": _sha256(_canonical_facts_bytes(facts)),
        "candidate_sha256": _sha256(candidate_raw),
        "lesson_markdown_sha256": _sha256(lesson_markdown_raw),
        "answer_book_markdown_sha256": _sha256(answer_book_markdown_raw),
        "lesson_status": "DRAFT",
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "general_fact_review": "NOT_RUN",
        "beginner_review": "NOT_RUN",
        "answer_book_review": "NOT_RUN",
    }
    try:
        validate_artifact(artifact)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return artifact


def general_unit_writer_packet(facts: Mapping[str, Any]) -> str:
    """Describe safe identity and the exact authored candidate fields to an operator."""
    unit = facts["selected_unit"]
    lines = [
        "# Phase 6D2A General Learning Writer Packet",
        "",
        "Status: DRAFT only. General factuality, beginner quality, and answer-book review are NOT_RUN.",
        "Teach only general concepts. Do not assert target-project behavior or add project claims, evidence, source locators, excerpts, or project references.",
        "This packet does not prove that any authored explanation or answer is true.",
        "",
        f"Curriculum unit: {unit['id']} — {unit['title']}",
        f"Origin: {unit['origin']}",
        f"Curriculum scope: {unit['scope']}",
        f"Prepared facts SHA-256: {_sha256(_canonical_facts_bytes(facts))}",
        f"Source status: {facts['source_status']}; unknown files: {facts['unknown_files']}",
        f"Prerequisite unit IDs: {', '.join(unit['prerequisite_ids']) or 'none'}",
        f"Required concepts (cover each exactly once): {', '.join(unit['requires_concept_keys']) or 'none'}",
        f"Introduced concepts (cover each exactly once): {', '.join(unit['introduces_concept_keys']) or 'none'}",
        "",
        "Create `general-unit-candidate.json` beside the prepared facts using the exact shape in `skills/project-deepdive/prompts/phase6d2a-general-learning-writer.md`.",
        "For a PREREQUISITE_PRIMER, copy primer_concept_key, prerequisite_node_id, and concept_epistemic_status; omit unit_key. For a CANDIDATE, copy unit_key and omit all primer fields.",
        "The learning_check question must stand alone for a beginner. Supply its explicit worked reference answer, progressive hints, and qualitative rubric; the lesson renders only the question.",
        "",
    ]
    return "\n".join(lines)
