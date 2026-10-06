#!/usr/bin/env python3
"""Pure Phase 6D1 answer projection and deterministic answer-book rendering."""

from __future__ import annotations

import copy
import hashlib
import html
import json
import re
from collections.abc import Mapping
from typing import Any

from artifact_contract import ArtifactValidationError, validate_artifact


_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ELIGIBLE_LEVELS = frozenset({"E0", "E1", "E2", "E3", "E4", "E5"})
_ANSWER_FIELDS = (
    "worked_steps", "progressive_hints", "common_mistakes", "rubric", "acceptable_tradeoffs",
)
_REQUIRED_FRAGMENT_FIELDS = ("worked_steps", "common_mistakes", "rubric")
_DIGEST_ROLES = (
    "chapter", "chapter_facts", "exercise_bank", "review_session", "review_status",
    "writer_packet", "answer_candidates",
)


class Phase6AnswerBookError(ValueError):
    """A fixed, redacted answer-candidate or answer-book failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise Phase6AnswerBookError(code)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _validate_package(package: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        facts = package.context.facts
        bank = package.context.bank
        session = package.session
        status = package.status
        raw = package.package_raw
        chapter_raw = package.context.chapter_raw
        facts_raw = package.context._facts_raw
        bank_raw = package.context._bank_raw
        session_raw = raw["chapter-review-session.json"]
        status_raw = raw["chapter-review-status.json"]
    except (AttributeError, KeyError, TypeError):
        _fail("INPUT_INVALID")

    try:
        validate_artifact(facts)
        validate_artifact(bank)
    except ArtifactValidationError:
        _fail("INPUT_INVALID")
    chapter_id = facts["selected_unit"]["chapter_id"]
    if (
        status.get("review_state") != "REVIEWED_DRAFT"
        or status.get("overall_status") != "PARTIAL"
        or facts["source_status"] not in {"PASS", "PARTIAL"}
        or bank.get("schema_version") != "1.0.0"
        or bank.get("chapter_id") != chapter_id
        or bank.get("repository_revision") != facts["repository_revision"]
        or bank.get("snapshot_kind") != facts["snapshot_kind"]
        or status.get("chapter_id") != chapter_id
        or session.get("chapter_id") != chapter_id
        or session.get("review_session_id") != status.get("review_session_id")
        or sha256(chapter_raw) != session.get("chapter_sha256")
        or sha256(facts_raw) != session.get("chapter_facts_sha256")
        or sha256(bank_raw) != session.get("exercise_bank_sha256")
        or sha256(session_raw) != status.get("review_session_sha256")
    ):
        _fail("REVIEW_NOT_READY")
    return facts, bank


def _eligible_claim_evidence(facts: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    evidence_by_id = {item["id"]: item for item in facts["evidence"]}
    result: dict[str, list[dict[str, Any]]] = {}
    for claim in facts["claims"]:
        if (
            claim["disposition"] != "SUPPORTED"
            or "E6" in claim["resolved_evidence_levels"]
        ):
            continue
        eligible: list[dict[str, Any]] = []
        for evidence_id in claim["evidence_ids"]:
            evidence = evidence_by_id.get(evidence_id)
            if (
                evidence is None
                or evidence["level"] not in _ELIGIBLE_LEVELS
                or evidence["provisional"]
            ):
                continue
            locator = evidence["locator"]
            safe_locator = {
                key: locator[key]
                for key in ("path", "symbol", "line_start", "line_end")
                if key in locator
            }
            eligible.append({"id": evidence_id, "level": evidence["level"], "locator": safe_locator})
        if eligible:
            result[claim["id"]] = eligible
    return result


def _citation_hint(evidence: Mapping[str, Any]) -> str:
    locator = evidence["locator"]
    parts: list[str] = []
    path = locator.get("path")
    if path:
        location = str(path)
        start = locator.get("line_start")
        end = locator.get("line_end")
        if start is not None:
            location += f":{start}" if start == end else f":{start}-{end}" if end is not None else f":{start}"
        parts.append(location)
    if locator.get("symbol"):
        parts.append(f"symbol {locator['symbol']}")
    return ", ".join(parts) or "locator not available"


def _escape_markdown_text(value: str) -> str:
    escaped = html.escape(value, quote=False)
    return re.sub(r"([\\`*_{}\[\]!#|~+])", r"\\\1", escaped)


def _blockquote(value: str) -> list[str]:
    lines = value.splitlines() or [value]
    return [f"> {_escape_markdown_text(line)}" if line else ">" for line in lines]


def _writer_packet(package: Any, facts: Mapping[str, Any], bank: Mapping[str, Any], writer_alias: str) -> bytes:
    eligible_by_claim = _eligible_claim_evidence(facts)
    lines = [
        "# Phase 6D1 answer-writer packet",
        "",
        f"Writer alias: `{writer_alias}`",
        f"Source snapshot: `{facts['repository_revision']}` (`{facts['snapshot_kind']}`)",
        f"Source status: {facts['source_status']}; unknown files: {facts['unknown_files']}.",
        "",
        "## What this packet asks you to write",
        "",
        "Complete each answer record in `answer-candidates-template.json`. Keep every question ID and prompt digest unchanged.",
        "Write in clear prose for a beginner. A fragment is one ordered piece of prose; it can contain more than one sentence.",
        "Use `GENERAL` only for non-project-specific teaching; it is not a way to make uncited project assertions. Use `CLAIM` only for a selected supported claim listed for that question below.",
        "If a question asks for a project fact that the listed claims do not support, state the evidence gap and do not guess. A new project claim needs upstream evidence and an updated authenticated facts package before it can be used.",
        "A claim ID is a reference to an authenticated project fact, not the fact text. Cite only the ID; do not copy source text or excerpts into this packet.",
        "Use only this invocation's exact B1-reviewed `chapter.md`, `chapter-facts.json`, and `exercise-bank.json`, plus the authenticated selected Phase 4C `claim-candidates` input identified in the digest list below. Do not mix review rounds, chapters, or snapshots.",
        "Use the metadata digests listed here and bound into the candidate template from this same invocation; do not recalculate or substitute them.",
        "Write an answer that directly answers the question with ordered worked reasoning. Make hints progress from a light nudge to a stronger cue; explain why each common mistake fails; make rubric criteria qualitatively observable; include tradeoffs only when they are genuinely meaningful.",
        "Replace every `[FILL_IN]` marker in the first four answer sections. `acceptable_tradeoffs` may stay empty when none is meaningful.",
        "Give progressive hints positive integer levels in strictly increasing order. The compiler checks shape and references, not truth, explanation quality, or beginner quality.",
        "Write only answer prose and allowed claim IDs into the candidate JSON. Do not paste source bodies, excerpts, secrets, credentials, or raw logs into candidates.",
        "The chapter review authenticates the chapter and question bank only. Answer evidence review and answer beginner review have not been run.",
        "",
        "## Authenticated input digests (this invocation)",
        f"- `repository_revision`: `{facts['repository_revision']}`; `snapshot_kind`: `{facts['snapshot_kind']}`.",
        f"- `source_run_manifest`: `{facts['source_run_manifest_sha256']}`.",
        "- B1 `chapter`: `" + sha256(package.context.chapter_raw) + "`.",
        "- B1 `chapter-facts`: `" + sha256(package.context._facts_raw) + "`.",
        "- B1 `exercise-bank`: `" + sha256(package.context._bank_raw) + "`.",
        "- B1 `review-session`: `" + sha256(package.package_raw['chapter-review-session.json']) + "`.",
        "- B1 `review-status`: `" + sha256(package.package_raw['chapter-review-status.json']) + "`.",
        "- The candidate template binds the canonical `writer_packet_sha256` for these exact packet bytes.",
        *[
            f"- `{item['role']}` (`{item['artifact_kind']}` {item['schema_version']}): `{item['sha256']}`."
            for item in facts["input_digests"]
        ],
        "",
        "## Source inspection boundary",
        *(
            ["Inspect project source only through the snapshot-aware Git reader at this exact `git-tree` revision; never inspect a mutable worktree or execute target code."]
            if facts["snapshot_kind"] == "git-tree"
            else ["This invocation is worktree-scoped: do not inspect project source separately. Use the reviewed chapter and authenticated facts, disclose unsupported gaps, and never execute target code."]
        ),
        "",
        "## Questions and eligible references",
    ]
    for question in bank["records"]:
        lines.extend(["", f"### `{question['id']}`", "", "Question (unchanged):", *_blockquote(question["prompt"]), ""])
        allowed = set(question["claim_ids"])
        references = [claim_id for claim_id in question["claim_ids"] if claim_id in eligible_by_claim]
        if not references:
            lines.append("Eligible supported claim references: none.")
            continue
        lines.append("Eligible supported claim references (ID and evidence locator only):")
        for claim_id in references:
            for evidence in eligible_by_claim[claim_id]:
                lines.append(
                    f"- `{claim_id}` — `{evidence['id']}` ({evidence['level']}; "
                    f"{_escape_markdown_text(_citation_hint(evidence))})"
                )
        if any(claim_id not in allowed for claim_id in references):
            _fail("INPUT_INVALID")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _bindings(package: Any, writer_alias: str, packet_raw: bytes) -> dict[str, Any]:
    facts, bank = _validate_package(package)
    if not isinstance(writer_alias, str) or not _ALIAS.fullmatch(writer_alias):
        _fail("INPUT_INVALID")
    return {
        "artifact_kind": "answer-candidates",
        "schema_version": "1.0.0",
        "repository_revision": facts["repository_revision"],
        "generated_at": bank["generated_at"],
        "snapshot_kind": facts["snapshot_kind"],
        "source_run_manifest_sha256": facts["source_run_manifest_sha256"],
        "chapter_id": facts["selected_unit"]["chapter_id"],
        "chapter_sha256": sha256(package.context.chapter_raw),
        "chapter_facts_sha256": sha256(package.context._facts_raw),
        "exercise_bank_sha256": sha256(package.context._bank_raw),
        "review_session_id": package.session["review_session_id"],
        "review_session_sha256": sha256(package.package_raw["chapter-review-session.json"]),
        "review_status_sha256": sha256(package.package_raw["chapter-review-status.json"]),
        "writer_packet_sha256": sha256(packet_raw),
        "writer_alias": writer_alias,
    }


def build_writer_material(package: Any, writer_alias: str) -> tuple[bytes, dict[str, Any]]:
    """Build the canonical writer packet and an intentionally unfinished JSON template."""
    facts, bank = _validate_package(package)
    if not isinstance(writer_alias, str) or not _ALIAS.fullmatch(writer_alias):
        _fail("INPUT_INVALID")
    packet = _writer_packet(package, facts, bank, writer_alias)
    identity = _bindings(package, writer_alias, packet)
    records = []
    for question in bank["records"]:
        placeholder = [{"kind": "GENERAL", "text": "[FILL_IN]"}]
        records.append({
            "question_id": question["id"],
            "prompt_sha256": question["prompt_sha256"],
            "worked_steps": copy.deepcopy(placeholder),
            "progressive_hints": [{"level": 1, "fragments": copy.deepcopy(placeholder)}],
            "common_mistakes": copy.deepcopy(placeholder),
            "rubric": copy.deepcopy(placeholder),
            "acceptable_tradeoffs": [],
        })
    return packet, {**identity, "records": records}


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _validate_candidate_bytes(candidate: Any, candidate_raw: bytes) -> None:
    if not isinstance(candidate_raw, bytes):
        _fail("CANDIDATE_INVALID")
    try:
        parsed = json.loads(candidate_raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if parsed != candidate:
            _fail("CANDIDATE_INVALID")
        validate_artifact(candidate)
    except Phase6AnswerBookError:
        raise
    except (ArtifactValidationError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail("CANDIDATE_INVALID")


def _validate_fragments(
    value: Any,
    *,
    required: bool,
    allowed_claims: set[str],
    eligible_claims: Mapping[str, list[dict[str, Any]]],
) -> list[str]:
    if not isinstance(value, list) or (required and not value):
        _fail("CANDIDATE_INVALID")
    used_claim_ids: list[str] = []
    for fragment in value:
        if not isinstance(fragment, dict) or fragment.get("kind") not in {"GENERAL", "CLAIM"}:
            _fail("CANDIDATE_INVALID")
        expected_keys = {"kind", "text"} if fragment["kind"] == "GENERAL" else {"kind", "text", "claim_id"}
        if set(fragment) != expected_keys:
            _fail("CANDIDATE_INVALID")
        text = fragment.get("text")
        if not isinstance(text, str) or not text.strip() or "[FILL_IN]" in text:
            _fail("CANDIDATE_INVALID")
        if fragment["kind"] == "CLAIM":
            claim_id = fragment["claim_id"]
            if claim_id not in allowed_claims or claim_id not in eligible_claims:
                _fail("CANDIDATE_INVALID")
            used_claim_ids.append(claim_id)
    return used_claim_ids


def _answers_for_question(
    candidate: Mapping[str, Any],
    question: Mapping[str, Any],
    eligible_claims: Mapping[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], list[str]]:
    answers = {key: copy.deepcopy(candidate[key]) for key in _ANSWER_FIELDS}
    used: list[str] = []
    allowed_claims = set(question["claim_ids"])
    for key in _REQUIRED_FRAGMENT_FIELDS:
        used.extend(_validate_fragments(
            answers[key], required=True, allowed_claims=allowed_claims, eligible_claims=eligible_claims,
        ))
    used.extend(_validate_fragments(
        answers["acceptable_tradeoffs"], required=False,
        allowed_claims=allowed_claims, eligible_claims=eligible_claims,
    ))
    levels: list[int] = []
    for hint in answers["progressive_hints"]:
        if (
            not isinstance(hint, dict)
            or set(hint) != {"level", "fragments"}
            or type(hint["level"]) is not int
            or hint["level"] <= 0
            or (levels and hint["level"] <= levels[-1])
        ):
            _fail("CANDIDATE_INVALID")
        levels.append(hint["level"])
        used.extend(_validate_fragments(
            hint["fragments"], required=True,
            allowed_claims=allowed_claims, eligible_claims=eligible_claims,
        ))
    return answers, used


def build_answer_bank(package: Any, candidate: Any, candidate_raw: bytes) -> dict[str, Any]:
    """Bind exact Writer bytes to the authenticated package and project the 1.1 bank."""
    facts, question_bank = _validate_package(package)
    _validate_candidate_bytes(candidate, candidate_raw)
    try:
        packet, _template = build_writer_material(package, candidate["writer_alias"])
        expected = _bindings(package, candidate["writer_alias"], packet)
    except (KeyError, TypeError):
        _fail("CANDIDATE_INVALID")
    if any(candidate.get(key) != value for key, value in expected.items()):
        _fail("PROVENANCE_MISMATCH")

    questions = question_bank["records"]
    question_by_id = {question["id"]: question for question in questions}
    if len(question_by_id) != len(questions):
        _fail("INPUT_INVALID")
    candidate_by_id: dict[str, Mapping[str, Any]] = {}
    for answer in candidate["records"]:
        question_id = answer["question_id"]
        if question_id in candidate_by_id:
            _fail("CANDIDATE_INVALID")
        candidate_by_id[question_id] = answer
    if set(candidate_by_id) != set(question_by_id):
        _fail("CANDIDATE_INVALID")

    eligible_claims = _eligible_claim_evidence(facts)
    records: list[dict[str, Any]] = []
    used_claim_ids: list[str] = []
    for question in questions:
        answer = candidate_by_id[question["id"]]
        if answer["prompt_sha256"] != question["prompt_sha256"]:
            _fail("PROVENANCE_MISMATCH")
        projected_answers, references = _answers_for_question(answer, question, eligible_claims)
        record = copy.deepcopy(question)
        record["answer_status"] = "AUTHORED"
        record.update(projected_answers)
        records.append(record)
        for claim_id in references:
            if claim_id not in used_claim_ids:
                used_claim_ids.append(claim_id)

    claim_catalog = [
        {"claim_id": claim_id, "evidence": copy.deepcopy(eligible_claims[claim_id])}
        for claim_id in used_claim_ids
    ]
    raw_digests = (
        ("chapter", package.context.chapter_raw),
        ("chapter_facts", package.context._facts_raw),
        ("exercise_bank", package.context._bank_raw),
        ("review_session", package.package_raw["chapter-review-session.json"]),
        ("review_status", package.package_raw["chapter-review-status.json"]),
        ("writer_packet", packet),
        ("answer_candidates", candidate_raw),
    )
    bank = {
        "artifact_kind": "exercise-bank",
        "schema_version": "1.1.0",
        "repository_revision": facts["repository_revision"],
        "generated_at": question_bank["generated_at"],
        "snapshot_kind": facts["snapshot_kind"],
        "source_run_manifest_sha256": facts["source_run_manifest_sha256"],
        "chapter_id": facts["selected_unit"]["chapter_id"],
        "curriculum_unit_id": question_bank["curriculum_unit_id"],
        "chapter_sha256": expected["chapter_sha256"],
        "chapter_facts_sha256": expected["chapter_facts_sha256"],
        "exercise_bank_sha256": expected["exercise_bank_sha256"],
        "review_session_id": package.session["review_session_id"],
        "review_session_sha256": expected["review_session_sha256"],
        "review_status_sha256": expected["review_status_sha256"],
        "writer_packet_sha256": expected["writer_packet_sha256"],
        "writer_alias": candidate["writer_alias"],
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "source_status": facts["source_status"],
        "unknown_files": facts["unknown_files"],
        "integrity_checks": {
            "input_bindings": "PASS",
            "question_identity": "PASS",
            "answer_shape": "PASS",
            "claim_reference_integrity": "PASS",
        },
        "answer_evidence_review": "NOT_RUN",
        "answer_beginner_review": "NOT_RUN",
        "input_digests": [{"role": role, "sha256": sha256(raw)} for role, raw in raw_digests],
        "claim_evidence_catalog": claim_catalog,
        "records": records,
    }
    try:
        validate_artifact(bank)
    except ArtifactValidationError:
        _fail("OUTPUT_INVALID")
    _validate_answer_bank(bank)
    return bank


def _validate_answer_bank(bank: Any) -> None:
    try:
        validate_artifact(bank)
    except ArtifactValidationError:
        _fail("INPUT_INVALID")
    if bank.get("schema_version") != "1.1.0" or bank.get("overall_status") != "PARTIAL":
        _fail("INPUT_INVALID")
    if {item["role"] for item in bank["input_digests"]} != set(_DIGEST_ROLES):
        _fail("INPUT_INVALID")
    catalog: dict[str, Mapping[str, Any]] = {}
    for entry in bank["claim_evidence_catalog"]:
        claim_id = entry["claim_id"]
        if claim_id in catalog or not entry["evidence"]:
            _fail("INPUT_INVALID")
        catalog[claim_id] = entry
    used: set[str] = set()
    question_ids: set[str] = set()
    for question in bank["records"]:
        if question["id"] in question_ids or question["answer_status"] != "AUTHORED":
            _fail("INPUT_INVALID")
        question_ids.add(question["id"])
        allowed = set(question["claim_ids"])
        for key in _REQUIRED_FRAGMENT_FIELDS:
            for claim_id in _validate_fragments(
                question[key], required=True, allowed_claims=allowed, eligible_claims=catalog,
            ):
                used.add(claim_id)
        for claim_id in _validate_fragments(
            question["acceptable_tradeoffs"], required=False,
            allowed_claims=allowed, eligible_claims=catalog,
        ):
            used.add(claim_id)
        levels: list[int] = []
        for hint in question["progressive_hints"]:
            if type(hint["level"]) is not int or hint["level"] <= 0 or (levels and hint["level"] <= levels[-1]):
                _fail("INPUT_INVALID")
            levels.append(hint["level"])
            for claim_id in _validate_fragments(
                hint["fragments"], required=True, allowed_claims=allowed, eligible_claims=catalog,
            ):
                used.add(claim_id)
    if used != set(catalog):
        _fail("INPUT_INVALID")


def _render_fragment(fragment: Mapping[str, Any], catalog: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = _blockquote(fragment["text"])
    if fragment["kind"] == "CLAIM":
        evidence_labels = [
            f"`{item['id']}` ({item['level']}; {_escape_markdown_text(_citation_hint(item))})"
            for item in catalog[fragment["claim_id"]]["evidence"]
        ]
        lines.append(f"> Evidence reference `{fragment['claim_id']}`: " + "; ".join(evidence_labels))
    return lines


def render_answer_book(bank: Mapping[str, Any]) -> str:
    """Render only the enriched bank, without opening source files or consulting a model."""
    _validate_answer_bank(bank)
    catalog = {item["claim_id"]: item for item in bank["claim_evidence_catalog"]}
    lines = [
        "# Answer Book",
        "",
        f"Answer status: **{bank['answer_book_status']} / {bank['overall_status']}**.",
        f"Source snapshot: `{bank['repository_revision']}` (`{bank['snapshot_kind']}`).",
        f"Source status: {bank['source_status']}; unknown files: {bank['unknown_files']}.",
        "This book does not establish source completeness or runtime execution. Answer evidence review and beginner review remain `NOT_RUN`.",
        "The chapter review covers the chapter and question bank only; it does not approve these answers.",
    ]
    for index, question in enumerate(bank["records"], start=1):
        lines.extend([
            "", f"## Question {index}: `{question['id']}`", "", "**Question (unchanged):**", *_blockquote(question["prompt"]),
            "", "### Worked steps",
        ])
        for step_index, fragment in enumerate(question["worked_steps"], start=1):
            lines.extend(["", f"#### Step {step_index}", *_render_fragment(fragment, catalog)])
        lines.extend(["", "### Progressive hints"])
        for hint in question["progressive_hints"]:
            lines.extend(["", f"#### Hint level {hint['level']}"])
            for fragment in hint["fragments"]:
                lines.extend(_render_fragment(fragment, catalog))
        lines.extend(["", "### Common mistakes"])
        for fragment in question["common_mistakes"]:
            lines.extend(["", *_render_fragment(fragment, catalog)])
        lines.extend(["", "### Rubric"])
        for fragment in question["rubric"]:
            lines.extend(["", *_render_fragment(fragment, catalog)])
        lines.extend(["", "### Acceptable tradeoffs"])
        if question["acceptable_tradeoffs"]:
            for fragment in question["acceptable_tradeoffs"]:
                lines.extend(["", *_render_fragment(fragment, catalog)])
        else:
            lines.extend(["", "No meaningful tradeoff is recorded for this question."])
    return "\n".join(lines) + "\n"


__all__ = [
    "Phase6AnswerBookError",
    "build_writer_material",
    "build_answer_bank",
    "render_answer_book",
    "sha256",
]
