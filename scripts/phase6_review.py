#!/usr/bin/env python3
"""Deterministic report coverage and aggregation for the Phase 6B1 handoff."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping
from urllib.parse import quote

from artifact_contract import ArtifactValidationError, validate_artifact
from phase4_graph import canonical_tuple_sha256
from phase6_chapter import (
    _CHAPTER_FORMAT_MARKER_PREFIX,
    _CHAPTER_V2_MARKER,
    _EXERCISE_LINE,
    _FOOTNOTE_LINE,
    _EXCERPT_LINE,
    _V2_SOURCE_LOCATION,
    _parse_v2_prose_line,
)


HEADINGS = (
    "Intuition", "Prerequisite", "Project use", "Architecture", "Source",
    "Mechanism", "Failure and debugging", "Extension", "Learning checks",
)
_CLAIM_LINE = re.compile(r"^Claim: (.+[.!?]) \[\^CLAIM-([0-9a-f]{64})\]$")
_GENERAL_LINE = re.compile(r"^General: .+[.!?]$")
_CLAIM_LEVELS = frozenset({"E0", "E1", "E2", "E3", "E4", "E5"})
_B2A_TEACHING_ISSUES = frozenset({"UNDEFINED_JARGON", "EXERCISE_UNCLEAR"})


class Phase6ReviewError(ValueError):
    """A fixed, redacted Phase 6B1 validation error."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise Phase6ReviewError(code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _chapter_lines(raw: bytes) -> list[tuple[int, str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        _fail("CHAPTER_INVALID")
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    v2 = len(lines) > 1 and lines[0] == "# Chapter" and lines[1] == _CHAPTER_V2_MARKER
    selected: list[tuple[int, str]] = []
    in_excerpt = False
    for number, line in enumerate(lines, start=1):
        if line.startswith("<!-- SOURCE-EXCERPT "):
            if v2 and _EXCERPT_LINE.fullmatch(line) is None:
                selected.append((number, line))
                continue
            in_excerpt = True
            continue
        if in_excerpt:
            if line == "<!-- /SOURCE-EXCERPT -->":
                in_excerpt = False
            continue
        selected.append((number, line))
    if in_excerpt:
        _fail("CHAPTER_INVALID")
    return selected


def expected_occurrences(context: Any) -> list[dict[str, Any]]:
    """Return each version-appropriate prose line occurrence without collapsing repeats."""
    lines = _chapter_lines(context.chapter_raw)
    facts_claims = {claim["id"]: claim for claim in context.facts["claims"]}
    results: list[dict[str, Any]] = []
    try:
        text = context.chapter_raw.decode("utf-8")
    except UnicodeError:
        _fail("CHAPTER_INVALID")
    raw_physical = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    v2_candidate = (
        len(raw_physical) > 1
        and raw_physical[0] == "# Chapter"
        and raw_physical[1] == _CHAPTER_V2_MARKER
    )
    structural_rows = (
        [(number - 1, line) for number, line in lines]
        if v2_candidate
        else list(enumerate(raw_physical))
    )
    marker_rows = [
        (index, line) for index, line in structural_rows
        if line.startswith(_CHAPTER_FORMAT_MARKER_PREFIX)
    ]
    v2 = v2_candidate and marker_rows == [(1, _CHAPTER_V2_MARKER)]
    if marker_rows and not v2:
        _fail("CHAPTER_INVALID")
    if v2:
        structural_physical = [line for _index, line in structural_rows]
        headings = [line[3:] for line in structural_physical if line.startswith("## ")]
        if headings != list(HEADINGS) or any(
            line.startswith("#") and not line.startswith("## ")
            for line in structural_physical[1:]
        ):
            _fail("CHAPTER_INVALID")
        current_heading = None
        exercise_state = 0
        footnotes_started = False
        for number, line in lines:
            if not line:
                continue
            if line == "# Chapter" or line == _CHAPTER_V2_MARKER:
                continue
            if line.startswith("## "):
                current_heading = line[3:]
                continue
            if line == "> Evidence gap — NOT ESTABLISHED IN THIS SLICE" and current_heading in HEADINGS[:-1]:
                continue
            if line.startswith("Source location:"):
                if current_heading != "Source" or _V2_SOURCE_LOCATION.fullmatch(line) is None:
                    _fail("CHAPTER_INVALID")
                continue
            if current_heading in HEADINGS[:-1]:
                parsed = _parse_v2_prose_line(line)
                if parsed is None:
                    if "[^" in line:
                        _fail("CHAPTER_INVALID")
                    _fail("CHAPTER_INVALID")
                kind, claim_id = parsed
                if kind == "CLAIM":
                    claim = facts_claims.get(claim_id)
                    if claim is None:
                        _fail("CHAPTER_INVALID")
                    evidence_ids = list(claim["evidence_ids"])
                else:
                    claim_id = None
                    evidence_ids = []
                line_sha = _sha256(line.encode("utf-8"))
                occurrence_id = "OCC-" + canonical_tuple_sha256((
                    "phase6-review-occurrence", context.facts["selected_unit"]["chapter_id"],
                    context.chapter_sha256, number, line_sha,
                ))
                results.append({
                    "occurrence_id": occurrence_id,
                    "line_number": number,
                    "line_sha256": line_sha,
                    "kind": kind,
                    "claim_id": claim_id,
                    "evidence_ids": evidence_ids,
                })
                continue
            if current_heading == "Learning checks":
                if _FOOTNOTE_LINE.fullmatch(line) is not None:
                    footnotes_started = True
                    continue
                if footnotes_started:
                    _fail("CHAPTER_INVALID")
                if exercise_state == 0 and _EXERCISE_LINE.fullmatch(line) is not None:
                    exercise_state = 1
                    continue
                if exercise_state == 1 and line.startswith("Question: ") and line[len("Question: "):].strip():
                    exercise_state = 2
                    continue
                if exercise_state == 2 and line == ":::":
                    exercise_state = 0
                    continue
                _fail("CHAPTER_INVALID")
            _fail("CHAPTER_INVALID")
        if exercise_state != 0:
            _fail("CHAPTER_INVALID")
        if not any(row["kind"] == "CLAIM" for row in results):
            _fail("CHAPTER_INVALID")
        return results

    for number, line in lines:
        claim_match = _CLAIM_LINE.fullmatch(line)
        if claim_match is not None:
            claim_id = "CLAIM-" + claim_match.group(2)
            claim = facts_claims.get(claim_id)
            if claim is None:
                _fail("CHAPTER_INVALID")
            kind = "CLAIM"
            evidence_ids = list(claim["evidence_ids"])
        elif _GENERAL_LINE.fullmatch(line) is not None:
            claim_id = None
            evidence_ids = []
            kind = "GENERAL"
        else:
            continue
        line_sha = _sha256(line.encode("utf-8"))
        occurrence_id = "OCC-" + canonical_tuple_sha256((
            "phase6-review-occurrence", context.facts["selected_unit"]["chapter_id"],
            context.chapter_sha256, number, line_sha,
        ))
        results.append({
            "occurrence_id": occurrence_id,
            "line_number": number,
            "line_sha256": line_sha,
            "kind": kind,
            "claim_id": claim_id,
            "evidence_ids": evidence_ids,
        })
    if not any(row["kind"] == "CLAIM" for row in results):
        _fail("CHAPTER_INVALID")
    return results


def expected_section_scopes(context: Any) -> list[dict[str, Any]]:
    """Return the exact nine heading scopes and one separate exercise-set scope."""
    lines = _chapter_lines(context.chapter_raw)
    scopes: list[dict[str, Any]] = []
    heading_lines: dict[str, tuple[int, str]] = {}
    for number, line in lines:
        if line.startswith("## "):
            heading_lines[line[3:]] = (number, _sha256(line.encode("utf-8")))
    if set(heading_lines) != set(HEADINGS):
        _fail("CHAPTER_INVALID")
    chapter_id = context.facts["selected_unit"]["chapter_id"]
    for heading in HEADINGS:
        number, line_sha = heading_lines[heading]
        scopes.append({
            "scope_id": "SECTION-" + canonical_tuple_sha256((
                "phase6-review-section", chapter_id, context.chapter_sha256, heading, number, line_sha,
            )),
            "scope_kind": "SECTION",
            "heading": heading,
            "line_number": number,
            "line_sha256": line_sha,
        })
    number, line_sha = heading_lines["Learning checks"]
    exercise_identity = [
        [record["id"], record["prompt_sha256"]]
        for record in context.bank["records"]
    ]
    scopes.append({
        "scope_id": "EXERCISES-" + canonical_tuple_sha256((
            "phase6-review-exercise-set", chapter_id, context.chapter_sha256, exercise_identity,
        )),
        "scope_kind": "EXERCISE_SET",
        "heading": "Exercise set",
        "line_number": number,
        "line_sha256": line_sha,
    })
    return scopes


def build_session_and_packets(context: Any, writer_alias: str) -> tuple[dict[str, Any], bytes, bytes]:
    """Create canonical session and safe, role-specific handoff packets."""
    prompt_versions = {"evidence_verifier": "1.0.0", "beginner_critic": "1.0.0"}
    role_digest_pairs = [
        list(pair)
        for pair in sorted((row["role"], row["sha256"]) for row in context.input_digests)
    ]
    review_id = "REVIEW-" + canonical_tuple_sha256((
        "phase6-review", context.facts["selected_unit"]["chapter_id"],
        context.repository_revision, context.snapshot_kind, context.source_metadata,
        role_digest_pairs, context.facts_sha256, context.chapter_sha256,
        context.bank_sha256, context.status_sha256, prompt_versions, writer_alias, 0,
    ))
    evidence_packet = _evidence_packet(context, review_id)
    beginner_packet = _beginner_packet(context, review_id)
    session = {
        "artifact_kind": "chapter-review-session",
        "schema_version": "1.0.0",
        "review_session_id": review_id,
        "review_round": 0,
        "repository_revision": context.repository_revision,
        "generated_at": context.facts["generated_at"],
        "snapshot_kind": context.snapshot_kind,
        "source_metadata": dict(context.source_metadata),
        "source_status": context.source_status,
        "source_run_manifest_sha256": context.source_run_manifest_sha256,
        "unknown_files": context.unknown_files,
        "phase3_member_digests": context.phase3_member_digests,
        "input_digests": context.input_digests,
        "writer_alias": writer_alias,
        "prompt_versions": prompt_versions,
        "chapter_id": context.facts["selected_unit"]["chapter_id"],
        "chapter_sha256": context.chapter_sha256,
        "chapter_facts_sha256": context.facts_sha256,
        "exercise_bank_sha256": context.bank_sha256,
        "phase6a_status_sha256": context.status_sha256,
        "evidence_packet_sha256": _sha256(evidence_packet),
        "beginner_packet_sha256": _sha256(beginner_packet),
    }
    try:
        validate_artifact(session)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return session, evidence_packet, beginner_packet


def _evidence_packet(context: Any, review_id: str) -> bytes:
    lines = [
        "# Phase 6B1 Evidence Verifier Packet",
        "",
        "Review the exact chapter bytes supplied with this packet. Do not edit the draft.",
        "This is a round-0 independent factual-support review, not a quality-gate PASS.",
        "",
        f"Review session: {review_id}",
        f"Chapter: {context.facts['selected_unit']['chapter_id']}",
        f"Chapter SHA-256: {context.chapter_sha256}",
        f"Repository revision: {context.repository_revision}",
        f"Snapshot: {context.snapshot_kind}; source status: {context.source_status}; unknown tracked files: {context.unknown_files}",
        f"Phase3 manifest SHA-256: {context.source_run_manifest_sha256}",
        "",
        "Inspect only the exact authenticated source snapshot. For git-tree, use the pinned revision/object reader; never inspect the current worktree. Do not execute target code.",
        "For each listed occurrence, judge wording against its existing evidence and locator. E1/E2 source records are not runtime behavior; E6 remains inference; unknown stays unknown.",
        "Return one result per occurrence in chapter order. Do not include prose, excerpts, credentials, paths outside the repository, or chain-of-thought in the JSON report.",
        "",
        "## Occurrences",
    ]
    claim_by_id = {claim["id"]: claim for claim in context.facts["claims"]}
    evidence_by_id = {item["id"]: item for item in context.facts["evidence"]}
    for occurrence in expected_occurrences(context):
        if occurrence["kind"] == "CLAIM":
            claim = claim_by_id[occurrence["claim_id"]]
            lines.append(
                f"- {occurrence['occurrence_id']} line={occurrence['line_number']} "
                f"line_sha256={occurrence['line_sha256']} "
                f"claim={claim['id']} disposition={claim['disposition']} "
                f"levels={','.join(claim['resolved_evidence_levels']) or 'none'} "
                f"evidence={','.join(occurrence['evidence_ids']) or 'none'}"
            )
            for evidence_id in occurrence["evidence_ids"]:
                item = evidence_by_id[evidence_id]
                locator = item["locator"]
                location = locator.get("path", "no relative path")
                if "symbol" in locator:
                    location += "#" + locator["symbol"]
                if "line_start" in locator:
                    location += f":{locator['line_start']}-{locator['line_end']}"
                location = quote(location, safe="/-._~#:")
                lines.append(f"  - {evidence_id} level={item['level']} locator={location}")
        else:
            lines.append(
                f"- {occurrence['occurrence_id']} line={occurrence['line_number']} "
                f"line_sha256={occurrence['line_sha256']} kind=GENERAL"
            )
    lines.extend([
        "",
        "Copy evidence-review-template.json to evidence-review.json and replace only its reviewer-owned FILL_IN placeholders.",
        "Occurrence IDs, line hashes, session binding, evidence IDs, and packet digest are precomputed; do not calculate or change them.",
        "Use only fixed issue codes and actions in phase6b-evidence-verifier.md. A report is an operator-declared review, not cryptographic identity proof.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def _beginner_packet(context: Any, review_id: str) -> bytes:
    lines = [
        "# Phase 6B1 Beginner Critic Packet",
        "",
        "Review the exact chapter bytes supplied separately. Do not edit the draft and do not judge factual truth.",
        "The declared reader is a true BEGINNER. This is a round-0 readability review, not a quality-gate PASS.",
        "",
        f"Review session: {review_id}",
        f"Chapter: {context.facts['selected_unit']['chapter_id']}",
        f"Chapter SHA-256: {context.chapter_sha256}",
        f"Selected stage/title: {context.facts['selected_unit']['stage']} / {json.dumps(context.facts['selected_unit']['title'], ensure_ascii=False)}",
        "Prerequisite concept keys: " + (", ".join(context.facts["selected_unit"]["prerequisite_concept_keys"]) or "none"),
        "Review each required heading and the full exercise set. Do not ask the learner questions in chat.",
        "Return fixed outcome/action codes only; do not persist free-form rationale, source excerpts, or operational transcripts.",
        "",
        "## Required review scopes",
    ]
    for scope in expected_section_scopes(context):
        lines.append(
            f"- scope_id={scope['scope_id']} scope_kind={scope['scope_kind']} "
            f"heading={json.dumps(scope['heading'], ensure_ascii=False)} "
            f"line={scope['line_number']} line_sha256={scope['line_sha256']}"
        )
    lines.extend([
        "",
        "## Reader-facing exercise prompts",
    ])
    for record in context.bank["records"]:
        prompt = json.dumps(record["prompt"], ensure_ascii=False)
        for separator in ("\u0085", "\u2028", "\u2029"):
            prompt = prompt.replace(separator, f"\\u{ord(separator):04x}")
        lines.append(f"- {record['id']}: {prompt}")
    lines.extend([
        "",
        "Copy beginner-review-template.json to beginner-review.json and replace only reviewer-owned FILL_IN placeholders.",
        "Scope IDs, line hashes, session binding, exercise context, and packet digest are precomputed; do not calculate or change them.",
        "Use only fixed issue codes and actions in phase6b-beginner-critic.md.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")


def build_review_templates(
    context: Any,
    session: Mapping[str, Any],
    session_raw: bytes,
    evidence_packet: bytes,
    beginner_packet: bytes,
) -> tuple[bytes, bytes]:
    """Build canonical authoring templates with deterministic fields prefilled."""
    common = {
        "generated_at": "FILL_IN_UTC_TIMESTAMP",
        "review_session_id": session["review_session_id"],
        "review_session_sha256": _sha256(session_raw),
        "review_round": 0,
        "repository_revision": session["repository_revision"],
        "snapshot_kind": session["snapshot_kind"],
        "source_run_manifest_sha256": session["source_run_manifest_sha256"],
        "chapter_id": session["chapter_id"],
        "chapter_sha256": session["chapter_sha256"],
        "chapter_facts_sha256": session["chapter_facts_sha256"],
        "exercise_bank_sha256": session["exercise_bank_sha256"],
        "reviewer_alias": "",
        "reviewer_session_id": "",
        "fresh_context_isolated": "FILL_IN_BOOLEAN",
    }
    evidence = {
        **common,
        "artifact_kind": "chapter-evidence-review",
        "schema_version": "1.0.0",
        "direct_source_inspection": "FILL_IN_BOOLEAN",
        "packet_sha256": _sha256(evidence_packet),
        "results": [
            {**occurrence, "outcome": "FILL_IN_REVIEWER_OUTCOME", "findings": None}
            for occurrence in expected_occurrences(context)
        ],
    }
    beginner = {
        **common,
        "artifact_kind": "chapter-beginner-review",
        "schema_version": "1.0.0",
        "packet_sha256": _sha256(beginner_packet),
        "results": [
            {**scope, "outcome": "FILL_IN_REVIEWER_OUTCOME", "findings": None}
            for scope in expected_section_scopes(context)
        ],
    }

    def serialize_template(value: Mapping[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")

    return serialize_template(evidence), serialize_template(beginner)


def bind_report(report: Mapping[str, Any], session: Mapping[str, Any], session_sha256: str) -> None:
    """Check shared report identity fields against this exact prepared session."""
    expected = {
        "review_session_id": session["review_session_id"],
        "review_session_sha256": session_sha256,
        "review_round": 0,
        "repository_revision": session["repository_revision"],
        "snapshot_kind": session["snapshot_kind"],
        "source_run_manifest_sha256": session["source_run_manifest_sha256"],
        "chapter_id": session["chapter_id"],
        "chapter_sha256": session["chapter_sha256"],
        "chapter_facts_sha256": session["chapter_facts_sha256"],
        "exercise_bank_sha256": session["exercise_bank_sha256"],
    }
    if any(report.get(key) != value for key, value in expected.items()):
        _fail("REPORT_BINDING_INVALID")


def validate_evidence_report(report: Mapping[str, Any], context: Any, packet_sha256: str) -> None:
    try:
        validate_artifact(report)
    except ArtifactValidationError:
        _fail("REPORT_INVALID")
    expected = expected_occurrences(context)
    if report.get("packet_sha256") != packet_sha256 or len(report["results"]) != len(expected):
        _fail("REPORT_COVERAGE_INVALID")
    claims = {claim["id"]: claim for claim in context.facts["claims"]}
    evidence = {item["id"]: item for item in context.facts["evidence"]}
    for actual, wanted in zip(report["results"], expected, strict=True):
        for key, value in wanted.items():
            if actual.get(key) != value:
                _fail("REPORT_COVERAGE_INVALID")
        findings = actual["findings"]
        _validate_findings(findings)
        if wanted["kind"] == "GENERAL":
            if any(finding["evidence_id"] is not None for finding in findings):
                _fail("REPORT_REFERENCE_INVALID")
            if actual["outcome"] == "GENERAL_TEACHING":
                if findings:
                    _fail("REPORT_INVALID")
            elif actual["outcome"] == "PROJECT_FACT_UNMARKED":
                if not any(
                    item["issue_code"] == "PROJECT_FACT_UNMARKED"
                    and item["recommended_action"] == "MARK_AS_CLAIM_OR_REWRITE_AS_GENERAL"
                    for item in findings
                ):
                    _fail("REPORT_INVALID")
            else:
                _fail("REPORT_INVALID")
            continue
        claim = claims[wanted["claim_id"]]
        if any(
            finding["evidence_id"] is not None
            and finding["evidence_id"] not in wanted["evidence_ids"]
            for finding in findings
        ):
            _fail("REPORT_REFERENCE_INVALID")
        if actual["outcome"] == "CONSISTENT_WITH_CITED_EVIDENCE":
            if findings or claim["disposition"] != "SUPPORTED":
                _fail("REPORT_INVALID")
            levels = set(claim["resolved_evidence_levels"])
            if not (levels & _CLAIM_LEVELS) or "E6" in levels:
                _fail("REPORT_INVALID")
            if any(evidence[item]["level"] == "E6" for item in wanted["evidence_ids"]):
                _fail("REPORT_INVALID")
        elif actual["outcome"] in {"NEEDS_REVISION", "UNABLE_TO_ASSESS"}:
            if not findings:
                _fail("REPORT_INVALID")
        else:
            _fail("REPORT_INVALID")


def validate_beginner_report(report: Mapping[str, Any], context: Any, packet_sha256: str) -> None:
    try:
        validate_artifact(report)
    except ArtifactValidationError:
        _fail("REPORT_INVALID")
    expected = expected_section_scopes(context)
    if report.get("packet_sha256") != packet_sha256 or report.get("results") is None or len(report["results"]) != len(expected):
        _fail("REPORT_COVERAGE_INVALID")
    valid_occurrences = {row["occurrence_id"] for row in expected_occurrences(context)}
    valid_exercises = {record["id"] for record in context.bank["records"]}
    section_ids = {row["scope_id"] for row in expected}
    for actual, wanted in zip(report["results"], expected, strict=True):
        if any(actual.get(key) != value for key, value in wanted.items()):
            _fail("REPORT_COVERAGE_INVALID")
        findings = actual["findings"]
        _validate_findings(findings)
        if actual["outcome"] == "FOLLOWABLE_FOR_DECLARED_BEGINNER":
            if findings:
                _fail("REPORT_INVALID")
        elif actual["outcome"] in {"NEEDS_REVISION", "UNABLE_TO_ASSESS"}:
            if not findings:
                _fail("REPORT_INVALID")
        else:
            _fail("REPORT_INVALID")
        for finding in findings:
            location_kind = finding["location_kind"]
            location_id = finding["location_id"]
            allowed = {
                "section": section_ids,
                "occurrence": valid_occurrences,
                "exercise": valid_exercises,
            }[location_kind]
            if location_id not in allowed:
                _fail("REPORT_REFERENCE_INVALID")


def _validate_findings(findings: list[Mapping[str, Any]]) -> None:
    canonical = sorted(
        findings,
        key=lambda item: (
            item["issue_code"], item.get("location_id", ""), item.get("evidence_id") or "",
            item["severity"], item["recommended_action"], item.get("location_kind", ""),
        ),
    )
    if list(findings) != canonical:
        _fail("REPORT_INVALID")


def aggregate_status(
    context: Any,
    session: Mapping[str, Any],
    session_sha256: str,
    evidence_report: Mapping[str, Any],
    evidence_sha256: str,
    beginner_report: Mapping[str, Any],
    beginner_sha256: str,
) -> dict[str, Any]:
    evidence_outcome = _evidence_outcome(evidence_report)
    beginner_outcome = _beginner_outcome(beginner_report)
    aliases = [session["writer_alias"], evidence_report["reviewer_alias"], beginner_report["reviewer_alias"]]
    sessions = [evidence_report["reviewer_session_id"], beginner_report["reviewer_session_id"]]
    contexts_distinct = (
        len(set(aliases)) == 3
        and len(set(sessions)) == 2
        and evidence_report["fresh_context_isolated"]
        and beginner_report["fresh_context_isolated"]
    )
    verifier_inspected = evidence_report["direct_source_inspection"]
    if not contexts_distinct:
        evidence_outcome = "UNABLE_TO_ASSESS"
        beginner_outcome = "UNABLE_TO_ASSESS"
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
    counts = {severity: sum(finding["severity"] == severity for finding in all_findings) for severity in ("BLOCKER", "MAJOR", "MINOR")}
    status = {
        "artifact_kind": "chapter-review-status",
        "schema_version": "1.0.0",
        "review_session_id": session["review_session_id"],
        "review_session_sha256": session_sha256,
        "review_round": 0,
        "repository_revision": context.repository_revision,
        "generated_at": context.facts["generated_at"],
        "snapshot_kind": context.snapshot_kind,
        "source_metadata": dict(context.source_metadata),
        "source_status": context.source_status,
        "source_run_manifest_sha256": context.source_run_manifest_sha256,
        "unknown_files": context.unknown_files,
        "chapter_id": session["chapter_id"],
        "chapter_sha256": context.chapter_sha256,
        "chapter_facts_sha256": context.facts_sha256,
        "exercise_bank_sha256": context.bank_sha256,
        "evidence_report_sha256": evidence_sha256,
        "beginner_report_sha256": beginner_sha256,
        "writer_alias": session["writer_alias"],
        "evidence_reviewer_alias": evidence_report["reviewer_alias"],
        "beginner_reviewer_alias": beginner_report["reviewer_alias"],
        "reviewer_contexts_distinct": contexts_distinct,
        "verifier_source_inspection": verifier_inspected,
        "evidence_outcome": evidence_outcome,
        "beginner_outcome": beginner_outcome,
        "review_state": state,
        "overall_status": "PARTIAL",
        "finding_counts": counts,
    }
    try:
        validate_artifact(status)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    return status


def classify_b2a_findings(
    *,
    snapshot_key: str,
    chapter_id: str,
    review_session_id: str,
    evidence_report: Mapping[str, Any],
    beginner_report: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Map authenticated fixed-code B1 findings to conservative B2a actions.

    Evidence findings stop upstream except for the exact unmarked-General
    case that can be made non-project-specific. Only the two named beginner
    issue codes are otherwise same-facts edits; unknown codes stop upstream.
    The Writer handoff uses code-bound safe actions rather than passing
    through the reviewer's free action choice.
    """
    result: list[dict[str, Any]] = []
    sources = (
        ("EVIDENCE", evidence_report, "occurrence_id"),
        ("BEGINNER", beginner_report, "scope_id"),
    )
    for report_role, report, bound_key in sources:
        for row in report.get("results", []):
            bound_id = row.get(bound_key)
            for finding_index, finding in enumerate(row.get("findings", [])):
                issue_code = finding.get("issue_code", "UNKNOWN")
                recommended_action = finding.get("recommended_action")
                evidence_id = finding.get("evidence_id")
                safe_general_rewrite = (
                    report_role == "EVIDENCE"
                    and row.get("kind") == "GENERAL"
                    and issue_code == "PROJECT_FACT_UNMARKED"
                    and recommended_action == "MARK_AS_CLAIM_OR_REWRITE_AS_GENERAL"
                    and evidence_id is None
                )
                classification = (
                    "TEACHING_EDIT_POSSIBLE"
                    if safe_general_rewrite or (
                        report_role == "BEGINNER" and issue_code in _B2A_TEACHING_ISSUES
                    )
                    else "UPSTREAM_CLAIM_REQUIRED"
                )
                location_kind = finding.get("location_kind")
                location_id = finding.get("location_id")
                finding_id = "FINDING-" + canonical_tuple_sha256((
                    "phase6b2-finding", snapshot_key, chapter_id, review_session_id,
                    report_role, bound_id, finding_index, issue_code, evidence_id,
                    location_kind, location_id,
                ))
                result.append({
                    "finding_id": finding_id,
                    "report_role": report_role,
                    "bound_id": bound_id,
                    "finding_index": finding_index,
                    "issue_code": issue_code,
                    "classification": classification,
                    "severity": finding.get("severity"),
                    "reported_action": recommended_action,
                    "evidence_id": evidence_id,
                    "location_kind": location_kind,
                    "location_id": location_id,
                })
    return result


def _evidence_outcome(report: Mapping[str, Any]) -> str:
    if not report["fresh_context_isolated"] or not report["direct_source_inspection"]:
        return "UNABLE_TO_ASSESS"
    outcomes = [row["outcome"] for row in report["results"]]
    if "UNABLE_TO_ASSESS" in outcomes:
        return "UNABLE_TO_ASSESS"
    if "NEEDS_REVISION" in outcomes or "PROJECT_FACT_UNMARKED" in outcomes:
        return "NEEDS_REVISION"
    if any(row["kind"] == "CLAIM" and row["outcome"] != "CONSISTENT_WITH_CITED_EVIDENCE" for row in report["results"]):
        return "NEEDS_REVISION"
    return "CONSISTENT_WITH_CITED_EVIDENCE"


def _beginner_outcome(report: Mapping[str, Any]) -> str:
    if not report["fresh_context_isolated"]:
        return "UNABLE_TO_ASSESS"
    outcomes = [row["outcome"] for row in report["results"]]
    if "UNABLE_TO_ASSESS" in outcomes:
        return "UNABLE_TO_ASSESS"
    if "NEEDS_REVISION" in outcomes:
        return "NEEDS_REVISION"
    return "FOLLOWABLE_FOR_DECLARED_BEGINNER"
