#!/usr/bin/env python3
"""Pure inventories and non-promoting review contracts for Phase 6D2A2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact


FACT_ISSUES = frozenset({
    "UNSUPPORTED_GENERAL_FACT", "CONTRADICTED_OR_OUTDATED", "MISSING_SCOPE_OR_CONDITION",
    "TARGET_PROJECT_ASSERTION", "SENSITIVE_OR_COPIED_CONTENT", "AUTHORITY_INADEQUATE",
    "UNABLE_TO_ASSESS",
})
BEGINNER_ISSUES = frozenset({
    "UNDEFINED_JARGON", "MISSING_PREREQUISITE", "UNCLEAR_EXPLANATION", "ORDER_OR_ABSTRACTION_JUMP",
    "QUESTION_NOT_SELF_CONTAINED", "ANSWER_NOT_RESPONSIVE", "HINT_NOT_PROGRESSIVE", "RUBRIC_NOT_ACTIONABLE",
})


class Phase6GeneralReviewError(ValueError):
    """A fixed, redacted general-content review contract failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise Phase6GeneralReviewError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _stable_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(raw)


def _inventory_digest(rows: list[Mapping[str, Any]]) -> str:
    return _stable_digest([{key: value for key, value in row.items() if key not in {"text", "source_texts"}} for row in rows])


def _text_sha256(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def expected_general_text_occurrences(artifact: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Inventory every authored prose occurrence, keeping repeated fields distinct."""
    try:
        unit_id = artifact["selected_unit"]["id"]
        canonical_sha = _sha256(dumps_artifact(dict(artifact)).encode("utf-8"))
        lesson = artifact["candidate"]["lesson"]
        raw_rows: list[tuple[str, str, list[str]]] = [
            ("lesson.intuition", lesson["intuition"], ["lesson.intuition"]),
        ]
        for index, row in enumerate(lesson["prerequisite_explanations"]):
            raw_rows.append((f"lesson.prerequisite_explanations[{index}]", row["explanation"], [f"lesson.prerequisite_concepts[{index}]" ]))
        for index, row in enumerate(lesson["concept_explanations"]):
            raw_rows.append((f"lesson.concept_explanations[{index}]", row["explanation"], [f"lesson.concepts_in_unit[{index}]" ]))
        raw_rows.extend([
            ("lesson.worked_example", lesson["worked_example"], ["lesson.worked_example"]),
            ("lesson.common_misconception", lesson["common_misconception"], ["lesson.common_misconception"]),
        ])
        check = lesson["learning_check"]
        raw_rows.append(("learning_check.question", check["question"], ["lesson.learning_check.question", "answer_book.question"]))
        raw_rows.append(("learning_check.reference_answer", check["reference_answer"], ["answer_book.reference_answer"]))
        for index, value in enumerate(check["hints"]):
            raw_rows.append((f"learning_check.hints[{index}]", value, [f"answer_book.hints[{index}]" ]))
        for index, value in enumerate(check["rubric"]):
            raw_rows.append((f"learning_check.rubric[{index}]", value, [f"answer_book.rubric[{index}]" ]))
        result = []
        for field, text, render_locations in raw_rows:
            if not isinstance(text, str) or not text:
                _fail("INVENTORY_INVALID")
            text_sha = _text_sha256(text)
            identity = ("phase6d2a2-text-occurrence", unit_id, canonical_sha, field, text_sha)
            result.append({
                "occurrence_id": "GL-OCC-" + _stable_digest(identity),
                "unit_id": unit_id,
                "field": field,
                "location": render_locations[0],
                "render_locations": render_locations,
                "text_sha256": text_sha,
                "text": text,
            })
        return result
    except Phase6GeneralReviewError:
        raise
    except (ArtifactValidationError, KeyError, TypeError, ValueError):
        _fail("INVENTORY_INVALID")


def expected_general_beginner_scopes(
    artifact: Mapping[str, Any], occurrences: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Inventory each lesson section, the ordered lesson, and its answer-book exercise."""
    unit_id = artifact["selected_unit"]["id"]
    canonical_sha = _sha256(dumps_artifact(dict(artifact)).encode("utf-8"))
    def selected(predicate) -> list[str]:
        return [row["occurrence_id"] for row in occurrences if predicate(row)]

    specs = [
        ("LESSON_SECTION", "intuition", selected(lambda row: row["field"] == "lesson.intuition")),
        ("LESSON_SECTION", "prerequisite_concepts", selected(lambda row: row["field"].startswith("lesson.prerequisite_explanations["))),
        ("LESSON_SECTION", "concepts_in_unit", selected(lambda row: row["field"].startswith("lesson.concept_explanations["))),
        ("LESSON_SECTION", "worked_example", selected(lambda row: row["field"] == "lesson.worked_example")),
        ("LESSON_SECTION", "common_misconception", selected(lambda row: row["field"] == "lesson.common_misconception")),
        ("LESSON_SECTION", "learning_check_question", selected(lambda row: row["field"] == "learning_check.question")),
        ("ORDERED_LESSON", "ordered_lesson", [row["occurrence_id"] for row in occurrences if "lesson." in " ".join(row["render_locations"])]),
        ("ANSWER_BOOK_EXERCISE", "answer_book_exercise", selected(lambda row: row["field"].startswith("learning_check."))),
    ]
    result = []
    for scope_kind, section, occurrence_ids in specs:
        source_texts = [row["text"] for row in occurrences if row["occurrence_id"] in occurrence_ids]
        scope_sha = _stable_digest(["phase6d2a2-scope-content", section, occurrence_ids,
                                    [row["text_sha256"] for row in occurrences if row["occurrence_id"] in occurrence_ids]])
        scope_id = "GL-SCOPE-" + _stable_digest(("phase6d2a2-beginner-scope", unit_id, canonical_sha, scope_kind, section, occurrence_ids, scope_sha))
        result.append({
            "scope_id": scope_id,
            "unit_id": unit_id,
            "scope_kind": scope_kind,
            "section": section,
            "occurrence_ids": occurrence_ids,
            "scope_sha256": scope_sha,
            "source_texts": source_texts,
        })
    return result


@dataclass(frozen=True)
class ReviewMaterial:
    session: dict[str, Any]
    factuality_packet_raw: bytes
    beginner_packet_raw: bytes
    occurrences: list[dict[str, Any]]
    scopes: list[dict[str, Any]]
    factuality_template_raw: bytes
    beginner_template_raw: bytes


def _report_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"text", "source_texts"}}


def _packet_common(session: Mapping[str, Any], role: str) -> list[str]:
    return [
        f"# Phase 6D2A2 {role} Review Packet",
        "",
        "This is an independent reviewer packet; no reviewer/provider is invoked by the CLI.",
        "The tool adds no target-source material. The untrusted Writer candidate and rendered Markdown below may themselves contain target-project assertions, copied text, or sensitive material. Inspect before externalizing; flag concerns without repeating the content.",
        "Do not execute target code, claim project evidence, infer unsupported target behavior, or expose secrets. This review does not promote the D2A draft.",
        f"Review session: {session['review_session_id']}",
        f"Unit: {session['selected_unit_id']} ({session['selected_unit_origin']}, {session['selected_unit_scope']})",
        f"Source: {session['repository_revision']} / {session['snapshot_kind']}; source_status={session['source_status']}; unknown_files={session['unknown_files']}",
        f"D2A content digests: facts={session['facts_sha256']} candidate={session['candidate_sha256']} artifact={session['unit_artifact_sha256']} lesson={session['lesson_markdown_sha256']} answer_book={session['answer_book_markdown_sha256']}",
        "",
    ]


def build_general_review_material(context: Any) -> ReviewMaterial:
    """Build deterministic, role-separated packets and schema-invalid templates."""
    artifact = context.artifact
    occurrences = expected_general_text_occurrences(artifact)
    scopes = expected_general_beginner_scopes(artifact, occurrences)
    facts = context.facts
    candidate = artifact["candidate"]
    identity = {
        "unit_id": facts["selected_unit"]["id"],
        "facts_sha256": _sha256(context.facts_raw),
        "candidate_sha256": _sha256(context.candidate_raw),
        "unit_artifact_sha256": _sha256(context.artifact_raw),
        "lesson_markdown_sha256": _sha256(context.lesson_raw),
        "answer_book_markdown_sha256": _sha256(context.answer_book_raw),
        "writer_packet_sha256": _sha256(context.writer_packet_raw),
        "occurrences_sha256": _inventory_digest(occurrences),
        "scopes_sha256": _inventory_digest(scopes),
    }
    review_id = "GENERAL-REVIEW-" + _stable_digest(("phase6d2a2-session", identity))
    base = {
        "artifact_kind": "general-review-session", "schema_version": "1.0.0",
        "review_session_id": review_id, "review_round": 0,
        "generated_at": facts["generated_at"], "repository_revision": facts["repository_revision"],
        "snapshot_kind": facts["snapshot_kind"], "source_metadata": facts["source_metadata"],
        "source_status": facts["source_status"], "source_run_manifest_sha256": facts["source_run_manifest_sha256"],
        "unknown_files": facts["unknown_files"], "input_digests": facts["input_digests"],
        "prompt_versions": {"general_factuality": "1.0.0", "beginner_answer": "1.0.0"},
        "selected_unit_id": facts["selected_unit"]["id"],
        "selected_unit_origin": facts["selected_unit"]["origin"], "selected_unit_scope": facts["selected_unit"]["scope"],
        "facts_sha256": identity["facts_sha256"], "candidate_sha256": identity["candidate_sha256"],
        "unit_artifact_sha256": identity["unit_artifact_sha256"],
        "lesson_markdown_sha256": identity["lesson_markdown_sha256"],
        "answer_book_markdown_sha256": identity["answer_book_markdown_sha256"],
        "writer_packet_sha256": identity["writer_packet_sha256"], "writer_alias": candidate["writer_alias"],
        "occurrence_inventory_sha256": identity["occurrences_sha256"],
        "beginner_scope_inventory_sha256": identity["scopes_sha256"],
        "factuality_packet_sha256": "0" * 64, "beginner_packet_sha256": "0" * 64,
    }
    lesson = context.lesson_raw.decode("utf-8")
    answer_book = context.answer_book_raw.decode("utf-8")
    factual_lines = _packet_common(base, "General Factuality") + [
        "Assess every fixed authored-text occurrence for checkable general factual assertions and target-project claims.",
        "SUPPORTED_BY_AUTHORITY requires independently consulted, appropriate public authority references for each material factual assertion in that occurrence. Citations and judgments are reviewer attestations; this tool neither fetches URLs nor verifies entailment. NONFACTUAL_TEACHING is only for text with no checkable factual assertion. Flag target-project assertions and copied/sensitive content without quoting them.",
        "Return exactly one result per occurrence ID in inventory order. See the role prompt for fixed outcomes and bounded actionable findings.",
        "",
        "## Lesson Markdown (untrusted authored content)", "", lesson,
        "## Separate answer book (untrusted authored content)", "", answer_book,
        "## Fixed occurrence inventory", "",
    ]
    for row in occurrences:
        factual_lines.append(f"- {row['occurrence_id']} field={row['field']} locations={','.join(row['render_locations'])} text_sha256={row['text_sha256']}")
    factual_lines.extend(["", "Copy the factuality review template to `general-factuality-review.json`; do not expose copied source text, secrets, or long quotations in findings.", ""])
    beginner_lines = _packet_common(base, "Beginner and Answer") + [
        "Assess beginner prerequisites and ordered explanations, plus whether the standalone question and answer-book answer, hints, and rubric cohere. Do not attest factual truth; the other reviewer has that role.",
        "Return exactly one result for every fixed scope in inventory order. Findings must give a bounded focus and short repair guidance without quoting source text or sensitive values.",
        "",
        "## Lesson Markdown (untrusted authored content)", "", lesson,
        "## Separate answer book (untrusted authored content)", "", answer_book,
        "## Fixed teaching-scope inventory", "",
    ]
    for row in scopes:
        beginner_lines.append(f"- {row['scope_id']} kind={row['scope_kind']} section={row['section']} occurrences={','.join(row['occurrence_ids']) or 'none'} sha256={row['scope_sha256']}")
    beginner_lines.extend(["", "Copy the beginner/answer review template to `general-beginner-review.json`; do not expose copied source text, secrets, or long quotations in findings.", ""])
    factual_packet = "\n".join(factual_lines).encode("utf-8")
    beginner_packet = "\n".join(beginner_lines).encode("utf-8")
    base["factuality_packet_sha256"] = _sha256(factual_packet)
    base["beginner_packet_sha256"] = _sha256(beginner_packet)
    try:
        validate_artifact(base)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    session_raw = dumps_artifact(base).encode("utf-8")
    binding = _report_binding(base, _sha256(session_raw), "")
    fact_template = {
        "artifact_kind": "general-factuality-review", "schema_version": "1.0.0", "generated_at": "FILL_IN_UTC_TIMESTAMP",
        **binding, "reviewer_alias": "", "reviewer_session_id": "", "fresh_context_isolated": "FILL_IN_BOOLEAN",
        "source_reference_inspection": "FILL_IN_BOOLEAN", "packet_sha256": _sha256(factual_packet),
        "results": [{**_report_row(row), "outcome": "FILL_IN_OUTCOME", "authority_references": [], "findings": None} for row in occurrences],
    }
    beginner_template = {
        "artifact_kind": "general-beginner-review", "schema_version": "1.0.0", "generated_at": "FILL_IN_UTC_TIMESTAMP",
        **binding, "reviewer_alias": "", "reviewer_session_id": "", "fresh_context_isolated": "FILL_IN_BOOLEAN",
        "packet_sha256": _sha256(beginner_packet),
        "results": [{**_report_row(row), "outcome": "FILL_IN_OUTCOME", "findings": None} for row in scopes],
    }
    serialize = lambda value: (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return ReviewMaterial(base, factual_packet, beginner_packet, occurrences, scopes, serialize(fact_template), serialize(beginner_template))


def _report_binding(session: Mapping[str, Any], session_sha: str, packet_sha: str) -> dict[str, Any]:
    keys = (
        "review_session_id", "review_round", "repository_revision", "snapshot_kind", "source_run_manifest_sha256",
        "selected_unit_id", "selected_unit_origin", "selected_unit_scope", "facts_sha256", "candidate_sha256",
        "unit_artifact_sha256", "lesson_markdown_sha256", "answer_book_markdown_sha256", "writer_alias",
    )
    return {"review_session_sha256": session_sha, **{key: session[key] for key in keys}, "packet_sha256": packet_sha}


def _validate_binding(report: Mapping[str, Any], session: Mapping[str, Any], session_raw: bytes, packet_raw: bytes) -> None:
    expected = _report_binding(session, _sha256(session_raw), _sha256(packet_raw))
    if any(report.get(key) != value for key, value in expected.items()):
        _fail("REPORT_BINDING_INVALID")


def _echoes_source(finding: Mapping[str, Any], text: str) -> bool:
    note = (finding["focus"] + " " + finding["repair_guidance"]).casefold()
    if text.casefold() in note and len(text) >= 20:
        return True
    # Reject any long verbatim run of authored prose in reviewer-owned notes.
    folded = text.casefold()
    return any(folded[index:index + 40] in note for index in range(max(0, len(folded) - 39)))


def _check_findings(findings: list[Mapping[str, Any]], *, expected_codes: frozenset[str], allowed_locations: set[str], source_texts: list[str]) -> None:
    previous = None
    for item in findings:
        if item["issue_code"] not in expected_codes or item["location_id"] not in allowed_locations:
            _fail("REPORT_REFERENCE_INVALID")
        if _echoes_source(item, "\n".join(source_texts)):
            _fail("REPORT_PRIVACY_INVALID")
        key = (item["issue_code"], item["location_id"], item["severity"], item["recommended_action"], item["focus"], item["repair_guidance"])
        if previous is not None and key < previous:
            _fail("REPORT_INVALID")
        previous = key


def validate_general_factuality_report(
    report: Mapping[str, Any], session: Mapping[str, Any], session_raw: bytes,
    packet_raw: bytes, occurrences: list[Mapping[str, Any]],
) -> None:
    try:
        validate_artifact(report)
    except ArtifactValidationError:
        _fail("REPORT_INVALID")
    _validate_binding(report, session, session_raw, packet_raw)
    results = report["results"]
    if len(results) != len(occurrences):
        _fail("REPORT_COVERAGE_INVALID")
    for actual, expected in zip(results, occurrences, strict=True):
        if any(actual.get(key) != value for key, value in _report_row(expected).items()):
            _fail("REPORT_COVERAGE_INVALID")
        outcome = actual["outcome"]
        findings = actual["findings"]
        references = actual["authority_references"]
        allowed = {expected["occurrence_id"]}
        _check_findings(findings, expected_codes=FACT_ISSUES, allowed_locations=allowed, source_texts=[expected["text"]])
        if outcome == "SUPPORTED_BY_AUTHORITY":
            if findings or not references or not report["source_reference_inspection"]:
                _fail("AUTHORITY_INVALID")
        elif outcome == "NONFACTUAL_TEACHING":
            if findings or references:
                _fail("REPORT_INVALID")
        elif outcome in {"NEEDS_REVISION", "UNABLE_TO_ASSESS"}:
            if not findings or references:
                _fail("REPORT_INVALID")
        else:
            _fail("REPORT_INVALID")
        reference_ids = [row["reference_id"] for row in references]
        if len(reference_ids) != len(set(reference_ids)):
            _fail("AUTHORITY_INVALID")


def validate_general_beginner_report(
    report: Mapping[str, Any], session: Mapping[str, Any], session_raw: bytes,
    packet_raw: bytes, scopes: list[Mapping[str, Any]],
) -> None:
    try:
        validate_artifact(report)
    except ArtifactValidationError:
        _fail("REPORT_INVALID")
    _validate_binding(report, session, session_raw, packet_raw)
    results = report["results"]
    if len(results) != len(scopes):
        _fail("REPORT_COVERAGE_INVALID")
    for actual, expected in zip(results, scopes, strict=True):
        if any(actual.get(key) != value for key, value in _report_row(expected).items()):
            _fail("REPORT_COVERAGE_INVALID")
        outcome = actual["outcome"]
        findings = actual["findings"]
        allowed = {expected["scope_id"], *expected["occurrence_ids"]}
        _check_findings(findings, expected_codes=BEGINNER_ISSUES, allowed_locations=allowed, source_texts=expected["source_texts"])
        if outcome == "FOLLOWABLE_FOR_BEGINNER":
            if findings:
                _fail("REPORT_INVALID")
        elif outcome in {"NEEDS_REVISION", "UNABLE_TO_ASSESS"}:
            if not findings:
                _fail("REPORT_INVALID")
        else:
            _fail("REPORT_INVALID")


def aggregate_general_review_status(
    context: Any,
    session: Mapping[str, Any],
    factuality: tuple[Mapping[str, Any] | None, bytes | None],
    beginner: tuple[Mapping[str, Any] | None, bytes | None],
    occurrences: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate declared scope and findings without asserting semantic truth."""
    fact_report, fact_raw = factuality
    beginner_report, beginner_raw = beginner
    fact_outcome = "MISSING_REPORT" if fact_report is None else _factuality_outcome(fact_report)
    beginner_outcome = "MISSING_REPORT" if beginner_report is None else _beginner_outcome(beginner_report)
    aliases = [session["writer_alias"]]
    reviewer_sessions: list[str] = []
    isolated = True
    if fact_report is not None:
        aliases.append(fact_report["reviewer_alias"])
        reviewer_sessions.append(fact_report["reviewer_session_id"])
        isolated &= fact_report["fresh_context_isolated"]
    if beginner_report is not None:
        aliases.append(beginner_report["reviewer_alias"])
        reviewer_sessions.append(beginner_report["reviewer_session_id"])
        isolated &= beginner_report["fresh_context_isolated"]
    contexts_distinct = (
        fact_report is not None and beginner_report is not None
        and len(set(aliases)) == 3 and len(set(reviewer_sessions)) == 2 and isolated
    )
    supported_count = sum(
        row["outcome"] == "SUPPORTED_BY_AUTHORITY" and bool(row["authority_references"])
        for row in (fact_report["results"] if fact_report else [])
    )
    incomplete = (
        fact_report is None or beginner_report is None or not contexts_distinct
        or fact_outcome == "UNABLE_TO_ASSESS" or beginner_outcome == "UNABLE_TO_ASSESS"
        or supported_count == 0
    )
    findings = [
        finding
        for report in (fact_report, beginner_report) if report is not None
        for result in report["results"] for finding in result["findings"]
    ]
    if incomplete:
        state = "REVIEW_INCOMPLETE"
    elif findings or fact_outcome == "NEEDS_REVISION" or beginner_outcome == "NEEDS_REVISION":
        state = "REPAIR_REQUIRED"
    else:
        state = "REVIEWED_DRAFT"
    session_sha = _sha256(dumps_artifact(dict(session)).encode("utf-8"))
    status = {
        "artifact_kind": "general-review-status", "schema_version": "1.0.0",
        "review_session_id": session["review_session_id"], "review_session_sha256": session_sha,
        "review_round": 0, "generated_at": session["generated_at"],
        "repository_revision": session["repository_revision"], "snapshot_kind": session["snapshot_kind"],
        "source_metadata": session["source_metadata"], "source_status": session["source_status"],
        "source_run_manifest_sha256": session["source_run_manifest_sha256"], "unknown_files": session["unknown_files"],
        "input_digests": session["input_digests"],
        "selected_unit_id": session["selected_unit_id"], "selected_unit_origin": session["selected_unit_origin"],
        "selected_unit_scope": session["selected_unit_scope"],
        "facts_sha256": session["facts_sha256"], "candidate_sha256": session["candidate_sha256"],
        "unit_artifact_sha256": session["unit_artifact_sha256"], "lesson_markdown_sha256": session["lesson_markdown_sha256"],
        "answer_book_markdown_sha256": session["answer_book_markdown_sha256"], "writer_packet_sha256": session["writer_packet_sha256"],
        "occurrence_inventory_sha256": session["occurrence_inventory_sha256"],
        "beginner_scope_inventory_sha256": session["beginner_scope_inventory_sha256"],
        "factuality_report_sha256": _sha256(fact_raw) if fact_raw is not None else None,
        "beginner_report_sha256": _sha256(beginner_raw) if beginner_raw is not None else None,
        "missing_roles": [role for role, value in (("GENERAL_FACTUALITY", fact_report), ("BEGINNER_ANSWER", beginner_report)) if value is None],
        "writer_alias": session["writer_alias"],
        "factuality_reviewer_alias": fact_report["reviewer_alias"] if fact_report else None,
        "beginner_reviewer_alias": beginner_report["reviewer_alias"] if beginner_report else None,
        "reviewer_contexts_distinct": contexts_distinct,
        "source_reference_inspection": fact_report["source_reference_inspection"] if fact_report else False,
        "supported_fact_occurrences": supported_count,
        "factuality_outcome": fact_outcome, "beginner_outcome": beginner_outcome,
        "lesson_status": "DRAFT", "answer_book_status": "DRAFT", "overall_status": "PARTIAL",
        "epistemic_status": "UNVERIFIED_TEACHING", "general_fact_review": "NOT_RUN",
        "beginner_review": "NOT_RUN", "answer_book_review": "NOT_RUN",
        "review_state": state,
        "finding_counts": {severity: sum(row["severity"] == severity for row in findings) for severity in ("BLOCKER", "MAJOR", "MINOR")},
    }
    try:
        validate_artifact(status)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return status


def _factuality_outcome(report: Mapping[str, Any]) -> str:
    outcomes = {row["outcome"] for row in report["results"]}
    if "UNABLE_TO_ASSESS" in outcomes:
        return "UNABLE_TO_ASSESS"
    if "NEEDS_REVISION" in outcomes:
        return "NEEDS_REVISION"
    return "SUPPORTED_BY_AUTHORITY" if "SUPPORTED_BY_AUTHORITY" in outcomes else "NONFACTUAL_TEACHING"


def _beginner_outcome(report: Mapping[str, Any]) -> str:
    outcomes = {row["outcome"] for row in report["results"]}
    if "UNABLE_TO_ASSESS" in outcomes:
        return "UNABLE_TO_ASSESS"
    return "NEEDS_REVISION" if "NEEDS_REVISION" in outcomes else "FOLLOWABLE_FOR_BEGINNER"
