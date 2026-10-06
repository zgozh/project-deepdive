"""Pure inventory and report contracts for Phase 6D1b answer review."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from artifact_contract import ArtifactValidationError, dumps_artifact, validate_artifact
from phase4_graph import canonical_tuple_sha256


_ANSWER_SECTIONS = (
    "worked_steps", "progressive_hints", "common_mistakes", "rubric", "acceptable_tradeoffs",
)
_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_EVIDENCE_ISSUES = frozenset({
    "CLAIM_UNSUPPORTED", "CLAIM_OVERGENERALIZED", "E6_PRESENTED_AS_FACT", "CITATION_MISMATCH",
    "SOURCE_SCOPE_MISMATCH", "LOCATOR_UNAVAILABLE", "PROJECT_FACT_UNMARKED", "OTHER_SUPPORT_GAP",
})


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _text_sha256(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def expected_answer_occurrences(bank: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return a fixed row for each answer fragment, including GENERAL prose."""
    evidence_by_claim = {
        item["claim_id"]: [row["id"] for row in item["evidence"]]
        for item in bank["claim_evidence_catalog"]
    }
    result: list[dict[str, Any]] = []
    for question in bank["records"]:
        question_id = question["id"]
        for section in _ANSWER_SECTIONS:
            fragments: list[tuple[int | None, int, Mapping[str, Any]]] = []
            if section == "progressive_hints":
                for hint in question[section]:
                    fragments.extend(
                        (hint["level"], index, fragment)
                        for index, fragment in enumerate(hint["fragments"])
                    )
            else:
                fragments.extend((None, index, fragment) for index, fragment in enumerate(question[section]))
            for hint_level, fragment_index, fragment in fragments:
                kind = fragment["kind"]
                claim_id = fragment.get("claim_id") if kind == "CLAIM" else None
                evidence_ids = evidence_by_claim.get(claim_id, []) if claim_id is not None else []
                text_sha = _text_sha256(fragment["text"])
                identity = (
                    "phase6d1b-answer-fragment", question_id, section, hint_level,
                    fragment_index, text_sha, kind, claim_id, evidence_ids,
                )
                result.append({
                    "occurrence_id": "A-OCC-" + canonical_tuple_sha256(identity),
                    "question_id": question_id,
                    "section": section,
                    "fragment_index": fragment_index,
                    "hint_level": hint_level,
                    "text_sha256": text_sha,
                    "kind": kind,
                    "claim_id": claim_id,
                    "evidence_ids": list(evidence_ids),
                })
    return result


def expected_beginner_scopes(
    bank: Mapping[str, Any], occurrences: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Return prompt and nonempty answer-section scopes for the beginner reviewer."""
    occurrences_by_question_section: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        occurrences_by_question_section[(occurrence["question_id"], occurrence["section"])].append(occurrence)

    result: list[dict[str, Any]] = []
    for question in bank["records"]:
        question_id = question["id"]
        scopes = [("question_prompt", "QUESTION_PROMPT", [], _text_sha256(question["prompt"]))]
        for section in _ANSWER_SECTIONS:
            selected = occurrences_by_question_section[(question_id, section)]
            if selected:
                occurrence_ids = [item["occurrence_id"] for item in selected]
                scope_sha = canonical_tuple_sha256((
                    "phase6d1b-answer-section-content",
                    [[item["text_sha256"], item["hint_level"], item["fragment_index"]] for item in selected],
                ))
                scopes.append((section, "ANSWER_SECTION", occurrence_ids, scope_sha))
        for section, scope_kind, occurrence_ids, scope_sha in scopes:
            scope_id = "A-SCOPE-" + canonical_tuple_sha256((
                "phase6d1b-answer-beginner-scope", question_id, section, occurrence_ids, scope_sha,
            ))
            result.append({
                "scope_id": scope_id,
                "question_id": question_id,
                "scope_kind": scope_kind,
                "section": section,
                "fragment_occurrence_ids": list(occurrence_ids),
                "scope_sha256": scope_sha,
            })
    return result


class Phase6AnswerReviewError(ValueError):
    """A fixed, redacted Phase 6D1b review contract or provenance failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise Phase6AnswerReviewError(code)


def _raw_sha256(raw: bytes) -> str:
    return _sha256(raw)


def _inventory_sha256(rows: list[Mapping[str, Any]]) -> str:
    raw = json.dumps(list(rows), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _raw_sha256(raw)


def _safe_locator(evidence: Mapping[str, Any]) -> str:
    locator = evidence.get("locator", {})
    path = locator.get("path")
    parts: list[str] = []
    if isinstance(path, str) and not path.startswith(("/", "\\")) and not re.match(r"^[A-Za-z]:", path):
        parts.append(quote(path, safe="/-._~"))
        start, end = locator.get("line_start"), locator.get("line_end")
        if isinstance(start, int):
            parts[-1] += f":{start}" + (f"-{end}" if isinstance(end, int) and end != start else "")
    symbol = locator.get("symbol")
    if isinstance(symbol, str):
        parts.append("symbol=" + quote(symbol, safe="._~-$"))
    return ", ".join(parts) or "locator unavailable"


def _json_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _evidence_packet(
    package: Any,
    session: Mapping[str, Any],
    bank: Mapping[str, Any],
    occurrences: list[Mapping[str, Any]],
) -> bytes:
    facts = package.context.facts
    catalog = {
        row["claim_id"]: row["evidence"]
        for row in bank["claim_evidence_catalog"]
    }
    question_by_id = {row["id"]: row for row in bank["records"]}
    lines = [
        "# Phase 6D1b Answer Evidence Verifier Packet",
        "",
        "Follow `skills/project-deepdive/prompts/phase6d1b-answer-evidence-verifier.md`; it defines the complete fixed review contract.",
        "Review the answer text against the exact authenticated snapshot. This is reviewer judgment; the checker validates only identity, fixed codes, and occurrence coverage.",
        "Inspect the original pinned source for every CLAIM fragment and decide whether each GENERAL fragment contains an unmarked project fact. A valid claim ID or eligible evidence ID is not proof that the wording is supported.",
        "For git-tree snapshots, inspect only the exact pinned revision through the snapshot-aware source reader. Never use the current worktree as a substitute and never execute project code.",
        "The packet contains answer prose and safe relative evidence locators only; it contains no source bodies. Do not copy source text, secrets, credentials, paths outside the repository, or review rationale into the report.",
        "Return one result for each occurrence in this exact order. Use only: CONSISTENT_WITH_CITED_EVIDENCE, NEEDS_REVISION, UNABLE_TO_ASSESS, GENERAL_TEACHING, PROJECT_FACT_UNMARKED.",
        "Fixed issue codes: " + ", ".join(sorted(_EVIDENCE_ISSUES)) + ".",
        "",
        f"Review session: {session['review_session_id']}",
        f"Answer book SHA-256: {session['answer_book_sha256']}",
        f"Source revision/snapshot: {session['repository_revision']} / {session['snapshot_kind']}",
        f"Source status: {session['source_status']}; unknown files: {session['unknown_files']}.",
        f"Phase3 manifest SHA-256: {session['source_run_manifest_sha256']}",
        "",
        "## Fragment inventory",
    ]
    for row in occurrences:
        question = question_by_id[row["question_id"]]
        if row["section"] == "progressive_hints":
            fragment = question[row["section"]][next(
                index for index, hint in enumerate(question[row["section"]])
                if hint["level"] == row["hint_level"]
            )]["fragments"][row["fragment_index"]]
        else:
            fragment = question[row["section"]][row["fragment_index"]]
        lines.extend([
            "",
            f"- occurrence={row['occurrence_id']} question={row['question_id']} section={row['section']} ",
            f"  fragment_index={row['fragment_index']} hint_level={row['hint_level']} kind={row['kind']} ",
            f"text_sha256={row['text_sha256']} claim={row['claim_id'] or 'none'}",
            f"  answer_text={_json_text(fragment['text'])}",
        ])
        if row["claim_id"]:
            for evidence in catalog[row["claim_id"]]:
                lines.append(
                    f"  eligible_reference={evidence['id']} level={evidence['level']} locator={_safe_locator(evidence)}"
                )
    lines.extend([
        "",
        "Copy `answer-evidence-review-template.json` to `answer-evidence-review.json`. Change only reviewer placeholders and findings; preserve all precomputed bindings, inventory identity fields, and order.",
        "Report only fixed codes and evidence IDs. The report is an operator-declared independent review, not cryptographic proof of reviewer identity or source truth.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def _beginner_packet(
    package: Any,
    session: Mapping[str, Any],
    bank: Mapping[str, Any],
    occurrences: list[Mapping[str, Any]],
    scopes: list[Mapping[str, Any]],
) -> bytes:
    facts = package.context.facts
    occurrences_by_id = {row["occurrence_id"]: row for row in occurrences}
    question_by_id = {row["id"]: row for row in bank["records"]}
    lines = [
        "# Phase 6D1b Answer Beginner Reviewer Packet",
        "",
        "Follow `skills/project-deepdive/prompts/phase6d1b-answer-beginner-reviewer.md`; it defines the complete fixed review contract.",
        "The declared reader is a true beginner. Independently review whether each question and answer can be understood and used by that reader. Do not judge source truth; the other role owns factual support.",
        "Check whether the question is legible, necessary prerequisites are explained, worked steps connect inputs to conclusions, hints progress from a light nudge to a stronger cue, common mistakes explain why they fail, and rubric criteria describe observable work. Check meaningful tradeoffs only when present.",
        "Flag undefined jargon, prerequisite jumps, skipped reasoning, weak hint progression, unexplained failure, and vague grading criteria. Return one result for every scope in order, using only fixed issue codes and actions.",
        "Do not rewrite answers, inspect project sources, copy credentials, or include free-text rationale in the report.",
        "",
        f"Review session: {session['review_session_id']}",
        f"Answer book SHA-256: {session['answer_book_sha256']}",
        f"Declared learner profile: BEGINNER; prerequisite concept keys: {', '.join(facts['selected_unit']['prerequisite_concept_keys']) or 'none'}.",
        "",
        "## Prompt and answer scopes",
    ]
    for scope in scopes:
        question = question_by_id[scope["question_id"]]
        lines.extend([
            "",
            f"- scope={scope['scope_id']} question={scope['question_id']} section={scope['section']} ",
            f"scope_kind={scope['scope_kind']} scope_sha256={scope['scope_sha256']}",
        ])
        if scope["section"] == "question_prompt":
            lines.append(f"  question_text={_json_text(question['prompt'])}")
        else:
            for occurrence_id in scope["fragment_occurrence_ids"]:
                occurrence = occurrences_by_id[occurrence_id]
                if occurrence["section"] == "progressive_hints":
                    hint = next(item for item in question["progressive_hints"] if item["level"] == occurrence["hint_level"])
                    fragment = hint["fragments"][occurrence["fragment_index"]]
                else:
                    fragment = question[occurrence["section"]][occurrence["fragment_index"]]
                lines.append(f"  occurrence={occurrence_id} text={_json_text(fragment['text'])}")
    lines.extend([
        "",
        "Copy `answer-beginner-review-template.json` to `answer-beginner-review.json`. Change only reviewer placeholders and findings; preserve all precomputed bindings, scope identity fields, and order.",
        "A complete positive report is only a reviewer judgment and cannot promote the answer book.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def build_review_session_and_packets(
    package: Any,
    candidate: Mapping[str, Any],
    candidate_raw: bytes,
    bank: Mapping[str, Any],
    bank_raw: bytes,
    answer_book_raw: bytes,
) -> tuple[dict[str, Any], bytes, bytes, list[dict[str, Any]], list[dict[str, Any]]]:
    """Bind one rebuilt D1 answer book and create the two role-separated packets."""
    context = package.context
    facts = context.facts
    occurrences = expected_answer_occurrences(bank)
    scopes = expected_beginner_scopes(bank, occurrences)
    evidence_inventory_sha = _inventory_sha256(occurrences)
    beginner_inventory_sha = _inventory_sha256(scopes)
    prompt_versions = {"evidence_verifier": "1.0.0", "beginner_reviewer": "1.0.0"}
    b1_session_raw = package.package_raw["chapter-review-session.json"]
    b1_status_raw = package.package_raw["chapter-review-status.json"]
    session_id = "ANSWER-REVIEW-" + canonical_tuple_sha256((
        "phase6d1b-answer-review",
        facts["selected_unit"]["chapter_id"],
        context.repository_revision,
        context.snapshot_kind,
        context.source_metadata,
        [[row["role"], row["sha256"]] for row in sorted(context.input_digests, key=lambda item: item["role"])],
        context.facts_sha256,
        context.chapter_sha256,
        context.bank_sha256,
        _raw_sha256(b1_session_raw),
        _raw_sha256(b1_status_raw),
        _raw_sha256(candidate_raw),
        _raw_sha256(bank_raw),
        _raw_sha256(answer_book_raw),
        prompt_versions,
        candidate["writer_alias"],
        0,
    ))
    session = {
        "artifact_kind": "answer-review-session",
        "schema_version": "1.0.0",
        "review_session_id": session_id,
        "review_round": 0,
        "generated_at": facts["generated_at"],
        "repository_revision": context.repository_revision,
        "snapshot_kind": context.snapshot_kind,
        "source_metadata": dict(context.source_metadata),
        "source_status": context.source_status,
        "source_run_manifest_sha256": context.source_run_manifest_sha256,
        "unknown_files": context.unknown_files,
        "phase3_member_digests": context.phase3_member_digests,
        "input_digests": context.input_digests,
        "prompt_versions": prompt_versions,
        "chapter_id": facts["selected_unit"]["chapter_id"],
        "chapter_sha256": context.chapter_sha256,
        "chapter_facts_sha256": context.facts_sha256,
        "exercise_bank_sha256": context.bank_sha256,
        "chapter_review_session_sha256": _raw_sha256(b1_session_raw),
        "chapter_review_status_sha256": _raw_sha256(b1_status_raw),
        "answer_candidates_sha256": _raw_sha256(candidate_raw),
        "answer_bank_sha256": _raw_sha256(bank_raw),
        "answer_book_sha256": _raw_sha256(answer_book_raw),
        "writer_alias": candidate["writer_alias"],
        "answer_book_status": bank["answer_book_status"],
        "overall_status": bank["overall_status"],
        "answer_evidence_review": bank["answer_evidence_review"],
        "answer_beginner_review": bank["answer_beginner_review"],
        "evidence_inventory_sha256": evidence_inventory_sha,
        "beginner_inventory_sha256": beginner_inventory_sha,
        "evidence_packet_sha256": "0" * 64,
        "beginner_packet_sha256": "0" * 64,
    }
    evidence_packet = _evidence_packet(package, session, bank, occurrences)
    beginner_packet = _beginner_packet(package, session, bank, occurrences, scopes)
    session["evidence_packet_sha256"] = _raw_sha256(evidence_packet)
    session["beginner_packet_sha256"] = _raw_sha256(beginner_packet)
    try:
        validate_artifact(session)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return session, evidence_packet, beginner_packet, occurrences, scopes


def _report_binding(session: Mapping[str, Any], session_sha256: str, packet_sha256: str) -> dict[str, Any]:
    keys = (
        "review_session_id", "review_round", "repository_revision", "snapshot_kind",
        "source_run_manifest_sha256", "chapter_id", "chapter_sha256", "chapter_facts_sha256",
        "exercise_bank_sha256", "chapter_review_session_sha256", "chapter_review_status_sha256",
        "answer_candidates_sha256", "answer_bank_sha256", "answer_book_sha256", "writer_alias",
    )
    return {
        "review_session_sha256": session_sha256,
        **{key: session[key] for key in keys},
        "packet_sha256": packet_sha256,
    }


def build_review_templates(
    session: Mapping[str, Any],
    session_raw: bytes,
    evidence_packet: bytes,
    beginner_packet: bytes,
    occurrences: list[Mapping[str, Any]],
    scopes: list[Mapping[str, Any]],
) -> tuple[bytes, bytes]:
    """Create schema-invalid reviewer templates with every identity row prefilled."""
    common = _report_binding(session, _raw_sha256(session_raw), "")
    evidence_template = {
        "artifact_kind": "answer-evidence-review",
        "schema_version": "1.0.0",
        "generated_at": "FILL_IN_UTC_TIMESTAMP",
        **common,
        "reviewer_alias": "",
        "reviewer_session_id": "",
        "fresh_context_isolated": "FILL_IN_BOOLEAN",
        "direct_source_inspection": "FILL_IN_BOOLEAN",
        "packet_sha256": _raw_sha256(evidence_packet),
        "results": [{**dict(row), "outcome": "FILL_IN_REVIEWER_OUTCOME", "findings": None} for row in occurrences],
    }
    beginner_template = {
        "artifact_kind": "answer-beginner-review",
        "schema_version": "1.0.0",
        "generated_at": "FILL_IN_UTC_TIMESTAMP",
        **common,
        "reviewer_alias": "",
        "reviewer_session_id": "",
        "fresh_context_isolated": "FILL_IN_BOOLEAN",
        "packet_sha256": _raw_sha256(beginner_packet),
        "results": [{**dict(row), "outcome": "FILL_IN_REVIEWER_OUTCOME", "findings": None} for row in scopes],
    }
    serialize = lambda value: (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return (
        serialize(evidence_template),
        serialize(beginner_template),
    )


def _validate_findings(findings: list[Mapping[str, Any]], *, beginner: bool) -> None:
    if beginner:
        key = lambda item: (
            item["issue_code"], item["location_id"], item["severity"], item["recommended_action"],
        )
    else:
        key = lambda item: (
            item["issue_code"], item["evidence_id"] or "", item["severity"], item["recommended_action"],
        )
    if list(findings) != sorted(findings, key=key):
        _fail("REPORT_INVALID")


def validate_evidence_report(
    report: Mapping[str, Any],
    session: Mapping[str, Any],
    session_sha256: str,
    packet_sha256: str,
    occurrences: list[Mapping[str, Any]],
) -> None:
    try:
        validate_artifact(report)
    except ArtifactValidationError:
        _fail("REPORT_INVALID")
    expected_binding = _report_binding(session, session_sha256, packet_sha256)
    if any(report.get(key) != value for key, value in expected_binding.items()):
        _fail("REPORT_BINDING_INVALID")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(occurrences):
        _fail("REPORT_COVERAGE_INVALID")
    for actual, wanted in zip(results, occurrences, strict=True):
        if any(actual.get(key) != value for key, value in wanted.items()):
            _fail("REPORT_COVERAGE_INVALID")
        findings = actual["findings"]
        _validate_findings(findings, beginner=False)
        if actual["kind"] == "GENERAL":
            if any(item["evidence_id"] is not None for item in findings):
                _fail("REPORT_REFERENCE_INVALID")
            if actual["outcome"] == "GENERAL_TEACHING":
                if findings:
                    _fail("REPORT_INVALID")
            elif actual["outcome"] == "PROJECT_FACT_UNMARKED":
                if not any(
                    item["issue_code"] == "PROJECT_FACT_UNMARKED"
                    and item["recommended_action"] == "MARK_AS_CLAIM_OR_REWRITE_GENERAL"
                    for item in findings
                ):
                    _fail("REPORT_INVALID")
            elif actual["outcome"] == "UNABLE_TO_ASSESS":
                if not findings:
                    _fail("REPORT_INVALID")
            else:
                _fail("REPORT_INVALID")
            continue
        if any(item["evidence_id"] is not None and item["evidence_id"] not in wanted["evidence_ids"] for item in findings):
            _fail("REPORT_REFERENCE_INVALID")
        if actual["outcome"] == "CONSISTENT_WITH_CITED_EVIDENCE":
            if findings or not actual["evidence_ids"]:
                _fail("REPORT_INVALID")
        elif actual["outcome"] in {"NEEDS_REVISION", "UNABLE_TO_ASSESS"}:
            if not findings:
                _fail("REPORT_INVALID")
        else:
            _fail("REPORT_INVALID")


def validate_beginner_report(
    report: Mapping[str, Any],
    session: Mapping[str, Any],
    session_sha256: str,
    packet_sha256: str,
    scopes: list[Mapping[str, Any]],
) -> None:
    try:
        validate_artifact(report)
    except ArtifactValidationError:
        _fail("REPORT_INVALID")
    expected_binding = _report_binding(session, session_sha256, packet_sha256)
    if any(report.get(key) != value for key, value in expected_binding.items()):
        _fail("REPORT_BINDING_INVALID")
    results = report.get("results")
    if not isinstance(results, list) or len(results) != len(scopes):
        _fail("REPORT_COVERAGE_INVALID")
    for actual, wanted in zip(results, scopes, strict=True):
        if any(actual.get(key) != value for key, value in wanted.items()):
            _fail("REPORT_COVERAGE_INVALID")
        findings = actual["findings"]
        _validate_findings(findings, beginner=True)
        if actual["outcome"] == "FOLLOWABLE_FOR_BEGINNER":
            if findings:
                _fail("REPORT_INVALID")
        elif actual["outcome"] in {"NEEDS_REVISION", "UNABLE_TO_ASSESS"}:
            if not findings:
                _fail("REPORT_INVALID")
        else:
            _fail("REPORT_INVALID")
        valid_location_ids = set(wanted["fragment_occurrence_ids"])
        valid_location_ids.add(wanted["scope_id"])
        if any(item["location_id"] not in valid_location_ids for item in findings):
            _fail("REPORT_REFERENCE_INVALID")


def _evidence_outcome(report: Mapping[str, Any]) -> str:
    outcomes = {row["outcome"] for row in report["results"]}
    if "UNABLE_TO_ASSESS" in outcomes:
        return "UNABLE_TO_ASSESS"
    if outcomes & {"NEEDS_REVISION", "PROJECT_FACT_UNMARKED"}:
        return "NEEDS_REVISION"
    return "CONSISTENT_WITH_CITED_EVIDENCE"


def _beginner_outcome(report: Mapping[str, Any]) -> str:
    outcomes = {row["outcome"] for row in report["results"]}
    if "UNABLE_TO_ASSESS" in outcomes:
        return "UNABLE_TO_ASSESS"
    if "NEEDS_REVISION" in outcomes:
        return "NEEDS_REVISION"
    return "FOLLOWABLE_FOR_BEGINNER"


def aggregate_status(
    package: Any,
    session: Mapping[str, Any],
    session_sha256: str,
    evidence_report: Mapping[str, Any],
    evidence_raw: bytes,
    beginner_report: Mapping[str, Any],
    beginner_raw: bytes,
) -> dict[str, Any]:
    """Summarize reviewer declarations/findings without changing source or D1 state."""
    evidence_outcome = _evidence_outcome(evidence_report)
    beginner_outcome = _beginner_outcome(beginner_report)
    aliases = [session["writer_alias"], evidence_report["reviewer_alias"], beginner_report["reviewer_alias"]]
    review_sessions = [evidence_report["reviewer_session_id"], beginner_report["reviewer_session_id"]]
    contexts_distinct = (
        len(set(aliases)) == 3
        and len(set(review_sessions)) == 2
        and evidence_report["fresh_context_isolated"]
        and beginner_report["fresh_context_isolated"]
    )
    verifier_inspected = evidence_report["direct_source_inspection"]
    if not contexts_distinct:
        evidence_outcome = "UNABLE_TO_ASSESS"
        beginner_outcome = "UNABLE_TO_ASSESS"
    elif not verifier_inspected:
        evidence_outcome = "UNABLE_TO_ASSESS"
    if (
        not contexts_distinct
        or not verifier_inspected
        or evidence_outcome == "UNABLE_TO_ASSESS"
        or beginner_outcome == "UNABLE_TO_ASSESS"
    ):
        state = "REVIEW_INCOMPLETE"
    elif evidence_outcome == "NEEDS_REVISION" or beginner_outcome == "NEEDS_REVISION":
        state = "REPAIR_REQUIRED"
    else:
        state = "REVIEWED_DRAFT"

    all_findings = [
        finding
        for report in (evidence_report, beginner_report)
        for result in report["results"]
        for finding in result["findings"]
    ]
    context = package.context
    facts = context.facts
    status = {
        "artifact_kind": "answer-review-status",
        "schema_version": "1.0.0",
        "review_session_id": session["review_session_id"],
        "review_session_sha256": session_sha256,
        "review_round": 0,
        "generated_at": facts["generated_at"],
        "repository_revision": context.repository_revision,
        "snapshot_kind": context.snapshot_kind,
        "source_metadata": dict(context.source_metadata),
        "source_status": context.source_status,
        "source_run_manifest_sha256": context.source_run_manifest_sha256,
        "unknown_files": context.unknown_files,
        "chapter_id": session["chapter_id"],
        "chapter_sha256": context.chapter_sha256,
        "chapter_facts_sha256": context.facts_sha256,
        "exercise_bank_sha256": context.bank_sha256,
        "chapter_review_session_sha256": session["chapter_review_session_sha256"],
        "chapter_review_status_sha256": session["chapter_review_status_sha256"],
        "answer_candidates_sha256": session["answer_candidates_sha256"],
        "answer_bank_sha256": session["answer_bank_sha256"],
        "answer_book_sha256": session["answer_book_sha256"],
        "evidence_report_sha256": _raw_sha256(evidence_raw),
        "beginner_report_sha256": _raw_sha256(beginner_raw),
        "writer_alias": session["writer_alias"],
        "evidence_reviewer_alias": evidence_report["reviewer_alias"],
        "beginner_reviewer_alias": beginner_report["reviewer_alias"],
        "reviewer_contexts_distinct": contexts_distinct,
        "verifier_source_inspection": verifier_inspected,
        "evidence_outcome": evidence_outcome,
        "beginner_outcome": beginner_outcome,
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "review_state": state,
        "finding_counts": {
            severity: sum(finding["severity"] == severity for finding in all_findings)
            for severity in ("BLOCKER", "MAJOR", "MINOR")
        },
    }
    try:
        validate_artifact(status)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return status
