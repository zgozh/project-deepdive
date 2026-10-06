#!/usr/bin/env python3
"""Deterministic Phase 6B2a finding triage and safe Writer handoff."""

from __future__ import annotations

from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from phase4_graph import canonical_tuple_sha256
from phase6_review import Phase6ReviewError, classify_b2a_findings


def build_triage(
    *,
    context: Any,
    session: Mapping[str, Any],
    evidence_report: Mapping[str, Any],
    beginner_report: Mapping[str, Any],
    status: Mapping[str, Any],
    session_sha256: str,
    status_sha256: str,
    evidence_report_sha256: str,
    beginner_report_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    """Create the complete triage artifact and Markdown from one authenticated package."""
    facts = context.facts
    chapter_id = facts["selected_unit"]["chapter_id"]
    snapshot_key = canonical_tuple_sha256((
        facts["repository_revision"], facts["snapshot_kind"], facts["source_metadata"],
    ))
    session_id = session["review_session_id"]
    triage_id = "TRIAGE-" + canonical_tuple_sha256((
        "phase6b2-triage", snapshot_key, chapter_id, session_id,
    ))
    findings = classify_b2a_findings(
        snapshot_key=snapshot_key,
        chapter_id=chapter_id,
        review_session_id=session_id,
        evidence_report=evidence_report,
        beginner_report=beginner_report,
    )
    teaching_count = sum(row["classification"] == "TEACHING_EDIT_POSSIBLE" for row in findings)
    upstream_count = len(findings) - teaching_count
    if upstream_count:
        disposition = "UPSTREAM_CLAIM_REQUIRED"
    elif teaching_count:
        disposition = "TEACHING_EDIT_POSSIBLE"
    else:
        disposition = "NO_SAME_FACTS_EDIT"
    artifact = {
        "artifact_kind": "chapter-repair-triage",
        "schema_version": "1.0.0",
        "repository_revision": facts["repository_revision"],
        "generated_at": session["generated_at"],
        "snapshot_kind": facts["snapshot_kind"],
        "source_metadata": dict(context.source_metadata),
        "source_status": context.source_status,
        "source_run_manifest_sha256": context.source_run_manifest_sha256,
        "unknown_files": context.unknown_files,
        "chapter_id": chapter_id,
        "chapter_sha256": context.chapter_sha256,
        "chapter_facts_sha256": context.facts_sha256,
        "exercise_bank_sha256": context.bank_sha256,
        "review_session_id": session_id,
        "review_session_sha256": session_sha256,
        "review_status_sha256": status_sha256,
        "evidence_report_sha256": evidence_report_sha256,
        "beginner_report_sha256": beginner_report_sha256,
        "review_state": status["review_state"],
        "overall_status": status["overall_status"],
        "triage_id": triage_id,
        "overall_disposition": disposition,
        "finding_counts": {
            "TEACHING_EDIT_POSSIBLE": teaching_count,
            "UPSTREAM_CLAIM_REQUIRED": upstream_count,
        },
        "findings": findings,
    }
    try:
        validate_artifact(artifact)
    except ArtifactValidationError as exc:
        raise Phase6ReviewError("OUTPUT_INVALID") from exc
    return artifact, render_writer_handoff(artifact)


def render_writer_handoff(artifact: Mapping[str, Any]) -> bytes:
    """Render only fixed enums and authenticated IDs; never include report prose."""
    lines = [
        "# Writer Handoff - Teaching-Only Triage",
        "",
        f"Triage: {artifact['triage_id']}",
        f"Disposition: {artifact['overall_disposition']}",
        f"Source status: {artifact['source_status']}; unknown tracked files: {artifact['unknown_files']}",
        "",
        "This handoff is not a chapter draft, a repaired result, or a review pass. Do not change claim text, claim IDs, evidence IDs, or project-fact scope under the current claim identity.",
        "",
    ]
    if artifact["overall_disposition"] == "UPSTREAM_CLAIM_REQUIRED":
        lines.extend([
            "## Stop gate",
            "",
            "An authenticated finding requires a new upstream claim/evidence audit and a new chapter package. Teaching-only edits below cannot clear this gate and must not be represented as a repaired chapter.",
            "",
        ])
    teaching = [
        finding for finding in artifact["findings"]
        if finding["classification"] == "TEACHING_EDIT_POSSIBLE"
    ]
    lines.extend(["## Same-facts teaching-only findings", ""])
    if teaching:
        lines.append(
            "These bounded edits may define an existing term, clarify a question, "
            "or rewrite an unmarked sentence without its project-specific assertion; "
            "they may not add project facts or answers:"
        )
        lines.append("")
        for finding in teaching:
            if finding["issue_code"] == "PROJECT_FACT_UNMARKED":
                safe_action = "REWRITE_AS_GENERAL_WITHOUT_PROJECT_ASSERTION"
                instruction = (
                    " Rewrite only as general teaching without the project-specific assertion. "
                    "Do not convert this sentence to a Claim or attach evidence references."
                )
            else:
                safe_action = {
                    "UNDEFINED_JARGON": "DEFINE_TERM_BEFORE_USE",
                    "EXERCISE_UNCLEAR": "REWRITE_EXERCISE",
                }[finding["issue_code"]]
                instruction = ""
            lines.append(
                f"- `{finding['finding_id']}` at `{finding['bound_id']}`: "
                f"`{finding['issue_code']}` / `{finding['severity']}` / `{safe_action}`."
                f"{instruction}"
            )
        lines.append("")
    else:
        lines.extend(["No same-facts Writer action is available from these findings.", ""])
    lines.extend([
        "Any future chapter must be a new Phase 6A output and pass its structural verifier before independent review. This B2a handoff does not authorize editing or overwrite any retained artifact.",
        "",
    ])
    return "\n".join(lines).encode("utf-8")
