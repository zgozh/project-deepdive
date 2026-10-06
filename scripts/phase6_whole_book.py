"""Pure selection, manifest, and Markdown rendering for Phase 6D3a."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact


class WholeBookError(ValueError):
    """A fixed, redacted whole-book selection or rendering failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity_sha(identity: Mapping[str, Any]) -> str:
    raw = json.dumps(identity, ensure_ascii=False, allow_nan=False, sort_keys=True, indent=2).encode("utf-8")
    return sha256(raw)


def validate_whole_book_selection(
    plan: Mapping[str, Any],
    states: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    selected_attempts: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Keep all plan rows and bind each explicit selection to its exact reviewed attempt."""
    if not states or not isinstance(selected_attempts, Mapping) or not selected_attempts:
        raise WholeBookError("SELECTION_INVALID")
    state = states[-1]
    schema_version = state.get("schema_version")
    if schema_version not in {"1.0.0", "1.1.0"}:
        raise WholeBookError("STATE_VERSION_UNSUPPORTED")
    plan_rows = plan.get("units")
    state_rows = state.get("units")
    if not isinstance(plan_rows, list) or not isinstance(state_rows, list) or len(plan_rows) != len(state_rows):
        raise WholeBookError("STATE_BINDING_INVALID")
    plan_by_id: dict[str, Mapping[str, Any]] = {}
    for row in plan_rows:
        identity = row.get("unit_identity")
        if not isinstance(identity, Mapping) or not isinstance(identity.get("id"), str) or identity["id"] in plan_by_id:
            raise WholeBookError("PLAN_INVALID")
        plan_by_id[identity["id"]] = row
    if any(unit_id not in plan_by_id for unit_id in selected_attempts):
        raise WholeBookError("UNIT_NOT_IN_PLAN")

    manifest_rows: list[dict[str, Any]] = []
    selected = set(selected_attempts)
    for position, (plan_row, state_row) in enumerate(zip(plan_rows, state_rows, strict=True), start=1):
        identity = plan_row["unit_identity"]
        unit_id = identity["id"]
        if (
            plan_row.get("position") != position
            or state_row.get("position") != position
            or state_row.get("unit_id") != unit_id
            or state_row.get("unit_identity_sha256") != _identity_sha(identity)
            or state_row.get("route") != plan_row.get("route")
            or state_row.get("plan_state") != plan_row.get("state")
            or state_row.get("prerequisite_ids") != identity.get("prerequisite_ids")
        ):
            raise WholeBookError("STATE_BINDING_INVALID")
        chosen_id = selected_attempts.get(unit_id)
        if chosen_id is not None:
            if plan_row.get("state") != "PLANNED" or plan_row.get("route") not in {"PHASE6A_PROJECT_CLAIM", "D2A_GENERAL_LEARNING"}:
                raise WholeBookError("UNIT_NOT_SELECTABLE")
            if schema_version == "1.0.0":
                if chosen_id != "attempt-0001":
                    raise WholeBookError("ATTEMPT_NOT_AVAILABLE")
                if state_row.get("attempt_id") != chosen_id or state_row.get("execution_state") != "REVIEWED_DRAFT":
                    raise WholeBookError("ATTEMPT_NOT_REVIEWED")
                attempt_digests = state_row.get("artifact_digests", [])
            else:
                attempts = state_row.get("attempts", [])
                matches = [row for row in attempts if row.get("attempt_id") == chosen_id]
                if len(matches) != 1:
                    raise WholeBookError("ATTEMPT_NOT_AVAILABLE")
                if matches[0].get("execution_state") != "REVIEWED_DRAFT":
                    raise WholeBookError("ATTEMPT_NOT_REVIEWED")
                attempt_digests = matches[0].get("artifact_digests", [])
            status_role = "chapter_review_status" if plan_row["route"] == "PHASE6A_PROJECT_CLAIM" else "general_review_status"
            status_entries = [item for item in attempt_digests if item.get("role") == status_role]
            if len(status_entries) != 1:
                raise WholeBookError("REVIEW_STATUS_BINDING_INVALID")
            review_state = "REVIEWED_DRAFT"
            status_digest = status_entries[0]["sha256"]
            digests = copy.deepcopy(attempt_digests)
        else:
            review_state = None
            status_digest = None
            digests = []
        manifest_rows.append({
            "position": position,
            "unit_id": unit_id,
            "unit_identity": copy.deepcopy(identity),
            "unit_identity_sha256": _identity_sha(identity),
            "route": plan_row["route"],
            "plan_state": plan_row["state"],
            "reason_codes": copy.deepcopy(plan_row["reason_codes"]),
            "blocked_by": copy.deepcopy(plan_row["blocked_by"]),
            "prerequisite_ids": copy.deepcopy(identity["prerequisite_ids"]),
            "execution_state": state_row["execution_state"],
            "selection_state": "SELECTED" if chosen_id else "NOT_SELECTED",
            "attempt_id": chosen_id,
            "review_state": review_state,
            "review_status_sha256": status_digest,
            "artifact_digests": digests,
            "answer_status": (
                "INCLUDED" if chosen_id and plan_row["route"] == "D2A_GENERAL_LEARNING"
                else "NOT_SUPPLIED" if chosen_id and plan_row["route"] == "PHASE6A_PROJECT_CLAIM"
                else "NOT_SELECTED"
            ),
        })
    for unit_id in selected:
        prerequisites = plan_by_id[unit_id]["unit_identity"].get("prerequisite_ids", [])
        if any(prerequisite not in selected for prerequisite in prerequisites):
            raise WholeBookError("PREREQUISITE_NOT_SELECTED")
    return manifest_rows


def _anchor(unit_id: str) -> str:
    return "unit-" + hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:16]


def _title(value: str) -> str:
    one_line = value.replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", one_line)


def render_whole_book(
    plan: Mapping[str, Any], rows: list[Mapping[str, Any]], bodies: Mapping[str, str],
) -> str:
    selected = [row for row in rows if row["selection_state"] == "SELECTED"]
    if not selected or any(row["unit_id"] not in bodies for row in selected):
        raise WholeBookError("SELECTED_CONTENT_MISSING")
    out = [
        "# Whole Book — Draft\n",
        "\n",
        f"> Snapshot kind: `{plan['snapshot_kind']}`; repository revision: `{plan['repository_revision']}`; "
        f"run plan: `{plan['run_plan_id']}`.\n",
        f"> Source status: **{plan['source_status']}**; unknown files: **{plan['unknown_files']}**. "
        "This D3a assembly is a draft and does not independently re-review content.\n",
        "\n",
        "## Contents\n",
        "\n",
    ]
    for row in selected:
        identity = row["unit_identity"]
        out.append(f"- [{_title(identity['title'])}](#{_anchor(row['unit_id'])})\n")
    out.extend(["\n", "## Lessons\n", "\n"])
    for row in selected:
        identity = row["unit_identity"]
        if row["route"] == "D2A_GENERAL_LEARNING":
            label = f"Teaching-only · origin={identity['origin']} · scope={identity['scope']}"
        else:
            label = "Project-specific"
        out.extend([
            f"<a id=\"{_anchor(row['unit_id'])}\"></a>\n",
            f"### {_title(identity['title'])}\n\n",
            f"**{label}** · `{identity['id']}` · `{row['attempt_id']}`\n\n",
        ])
        prerequisites = identity.get("prerequisite_ids", [])
        if prerequisites:
            out.append("Prerequisite unit IDs: " + ", ".join(f"`{item}`" for item in prerequisites) + "\n\n")
        concepts = identity.get("requires_concept_keys", [])
        if concepts:
            out.append("Required concepts: " + ", ".join(f"`{item}`" for item in concepts) + "\n\n")
        body = bodies[row["unit_id"]]
        out.append(body)
        if not body.endswith("\n"):
            out.append("\n")
        out.append("\n")
    return "".join(out)


def render_answer_book(
    rows: list[Mapping[str, Any]], answers: Mapping[str, str],
) -> str:
    out = ["# Whole Answer Book — Draft\n\n", "> Answers are assembled from the explicitly selected unit attempts.\n\n"]
    found = False
    for row in rows:
        unit_id = row["unit_id"]
        if row["selection_state"] != "SELECTED" or unit_id not in answers:
            continue
        found = True
        out.extend([f"## {_title(row['unit_identity']['title'])}\n\n", f"`{unit_id}` · `{row['attempt_id']}`\n\n", answers[unit_id]])
        if not answers[unit_id].endswith("\n"):
            out.append("\n")
        out.append("\n")
    if not found:
        out.append("No selected unit has an available answer book.\n")
    return "".join(out)


def render_quality_and_gaps(
    plan: Mapping[str, Any], rows: list[Mapping[str, Any]],
    *, project_answer_states: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    answers = project_answer_states or {}
    out = [
        "# Quality and Gaps — D3a\n\n",
        f"- Overall: `PARTIAL`\n- Lesson: `DRAFT`\n- Answer book: `DRAFT`\n- Cross-chapter audit: `NOT_RUN`\n",
        f"- Source: `{plan['source_status']}`; unknown files: `{plan['unknown_files']}`\n\n",
        "## Unit inventory\n\n",
    ]
    for row in rows:
        out.append(
            f"- `{row['unit_id']}`: {row['selection_state']}; route `{row['route']}`; "
            f"execution `{row['execution_state']}`; reasons `{', '.join(row['reason_codes']) or 'none'}`.\n"
        )
        if row["selection_state"] == "SELECTED" and row["route"] == "PHASE6A_PROJECT_CLAIM":
            answer = answers.get(row["unit_id"], {})
            answer_state = answer.get("answer_status", "NOT_SUPPLIED")
            review_state = answer.get("d1b_review_state", "NOT_RUN")
            out.append(
                f"  - Project answer package: `{answer_state}`; D1b review: `{review_state}`. "
                "D4 must not infer complete exercise coverage.\n"
            )
    out.extend([
        "\n## Review boundaries\n\n",
        "Selected attempts retain their recorded `REVIEWED_DRAFT` status; D3a performs integrity and provenance checks, not an independent semantic re-review.\n",
        "Project D1b review remains `NOT_RUN` unless an explicitly supplied and authenticated package says otherwise.\n",
        "Writer-authored Markdown is preserved verbatim and may contain sensitive content; D3a does not perform a privacy review.\n",
    ])
    return "".join(out)


def build_whole_book_manifest(
    plan: Mapping[str, Any], plan_raw: bytes, state: Mapping[str, Any], state_raw: bytes,
    rows: list[Mapping[str, Any]], output_bytes: Mapping[str, bytes],
    *, project_answers: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if set(output_bytes) != {"WHOLE-BOOK.md", "WHOLE-ANSWER-BOOK.md", "QUALITY-AND-GAPS.md"}:
        raise WholeBookError("OUTPUT_SET_INVALID")
    selected_count = sum(row["selection_state"] == "SELECTED" for row in rows)
    seed = json.dumps({
        "plan": sha256(plan_raw), "state": sha256(state_raw),
        "selection": [(row["unit_id"], row["attempt_id"]) for row in rows if row["selection_state"] == "SELECTED"],
        "project_answers": project_answers or {},
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")
    manifest_rows = copy.deepcopy(rows)
    for row in manifest_rows:
        if row["unit_id"] in (project_answers or {}):
            answer = copy.deepcopy(project_answers[row["unit_id"]])
            row["project_answer"] = answer
            row["answer_status"] = "INCLUDED"
        elif row["selection_state"] != "SELECTED":
            row["answer_status"] = "NOT_SELECTED"
        elif row["route"] == "PHASE6A_PROJECT_CLAIM":
            row["project_answer"] = None
            row["answer_status"] = "NOT_SUPPLIED"
    manifest = {
        "artifact_kind": "whole-book-assembly",
        "schema_version": "1.0.0",
        "repository_revision": plan["repository_revision"],
        "generated_at": plan["generated_at"],
        "assembly_id": "WHOLE-BOOK-ASSEMBLY-" + sha256(seed),
        "run_plan_id": plan["run_plan_id"],
        "run_plan_sha256": sha256(plan_raw),
        "run_state_id": state["run_state_id"],
        "run_state_revision": state["state_revision"],
        "run_state_sha256": sha256(state_raw),
        "run_state_schema_version": state["schema_version"],
        "snapshot_kind": plan["snapshot_kind"],
        "source_metadata": copy.deepcopy(plan["source_metadata"]),
        "source_status": plan["source_status"],
        "source_run_manifest_sha256": plan["source_run_manifest_sha256"],
        "unknown_files": plan["unknown_files"],
        "curriculum_schema_version": plan["curriculum_schema_version"],
        "curriculum_sha256": plan["curriculum_sha256"],
        "input_digests": copy.deepcopy(plan["input_digests"]),
        "unit_count": plan["unit_count"],
        "selected_count": selected_count,
        "units": manifest_rows,
        "lesson_status": "DRAFT",
        "answer_book_status": "DRAFT",
        "overall_status": "PARTIAL",
        "cross_chapter_audit": "NOT_RUN",
        "outputs": [
            {"role": role, "path": name, "sha256": sha256(output_bytes[name])}
            for role, name in (
                ("whole_book", "WHOLE-BOOK.md"),
                ("answer_book", "WHOLE-ANSWER-BOOK.md"),
                ("quality_and_gaps", "QUALITY-AND-GAPS.md"),
            )
        ],
    }
    try:
        validate_artifact(manifest)
    except ArtifactValidationError as exc:
        raise WholeBookError("MANIFEST_INVALID") from exc
    return manifest
