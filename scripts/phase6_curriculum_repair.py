#!/usr/bin/env python3
"""Conservative, non-publishing D2B2b finding classification."""

from __future__ import annotations

import copy
from typing import Any, Mapping, Sequence

from phase4_graph import canonical_tuple_sha256
from phase6_review import classify_b2a_findings


_D2A_BEGINNER_REPAIR_CODES = frozenset({
    "UNDEFINED_JARGON", "MISSING_PREREQUISITE", "UNCLEAR_EXPLANATION",
    "ORDER_OR_ABSTRACTION_JUMP", "QUESTION_NOT_SELF_CONTAINED", "ANSWER_NOT_RESPONSIVE",
    "HINT_NOT_PROGRESSIVE", "RUBRIC_NOT_ACTIONABLE",
})
_D2A_UPSTREAM_CODES = frozenset({"TARGET_PROJECT_ASSERTION"})
_D2A_FACTUALITY_REPAIR_TUPLES = frozenset({
    ("MISSING_SCOPE_OR_CONDITION", "NARROW_OR_CORRECT_CLAIM"),
    ("UNSUPPORTED_GENERAL_FACT", "REWRITE_AS_NONFACTUAL_TEACHING"),
})


def _result(disposition: str, findings: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"disposition": disposition, "findings": findings or []}


def _general_findings(
    status: Mapping[str, Any], reports: Mapping[str, Mapping[str, Any]], *, run_state_id: str,
    prerequisite_ids: Sequence[str],
) -> tuple[list[dict[str, Any]], bool]:
    findings: list[dict[str, Any]] = []
    unsupported = False
    upstream = False
    for role, report, bound_key in (
        ("FACTUALITY", reports["factuality"], "occurrence_id"),
        ("BEGINNER", reports["beginner"], "scope_id"),
    ):
        for row in report.get("results", []):
            bound_id = row.get(bound_key)
            for index, finding in enumerate(row.get("findings", [])):
                code = finding.get("issue_code")
                classification = "STOP_UNSUPPORTED"
                if role == "BEGINNER" and code in _D2A_BEGINNER_REPAIR_CODES:
                    classification = "TEACHING_EDIT_POSSIBLE"
                    if code == "MISSING_PREREQUISITE" and not prerequisite_ids:
                        classification = "STOP_UNSUPPORTED"
                elif role == "FACTUALITY" and (code, finding.get("recommended_action")) in _D2A_FACTUALITY_REPAIR_TUPLES:
                    classification = "TEACHING_EDIT_POSSIBLE"
                elif role == "FACTUALITY" and code in _D2A_UPSTREAM_CODES:
                    classification = "UPSTREAM_REQUIRED"
                if classification == "STOP_UNSUPPORTED":
                    unsupported = True
                elif classification == "UPSTREAM_REQUIRED":
                    upstream = True
                finding_id = "FINDING-" + canonical_tuple_sha256((
                    "phase6d2b2-general-finding", run_state_id, status["review_session_id"],
                    role, bound_id, index, code, finding.get("location_id"),
                ))
                findings.append({
                    "finding_id": finding_id, "report_role": role, "bound_id": bound_id,
                    "finding_index": index, "issue_code": code, "classification": classification,
                })
    return findings, unsupported or upstream


def classify_repair_findings(
    route: str,
    status: Mapping[str, Any],
    reports: Mapping[str, Mapping[str, Any]],
    *,
    run_state_id: str,
    prerequisite_ids: Sequence[str],
) -> dict[str, Any]:
    """Classify a previously authenticated, finalized status and its exact reports.

    Callers must authenticate report/status bytes and source freshness first. This
    pure function never reads files, writes state, or interprets reviewer prose.
    """
    review_state = status.get("review_state")
    if review_state == "REVIEWED_DRAFT":
        return _result("NO_REPAIR")
    if review_state == "REVIEW_INCOMPLETE":
        return _result("REVIEW_INCOMPLETE")
    if review_state != "REPAIR_REQUIRED":
        return _result("STOP_UNSUPPORTED")

    if route == "PHASE6A_PROJECT_CLAIM":
        evidence = reports.get("evidence")
        beginner = reports.get("beginner")
        if evidence is None or beginner is None:
            return _result("REPORTS_MISSING")
        classified = classify_b2a_findings(
            snapshot_key=run_state_id,
            chapter_id=status["chapter_id"],
            review_session_id=status["review_session_id"],
            evidence_report=evidence,
            beginner_report=beginner,
        )
        if not classified:
            return _result("STOP_UNSUPPORTED")
        if any(item["classification"] == "UPSTREAM_CLAIM_REQUIRED" for item in classified):
            return _result("UPSTREAM_REQUIRED", classified)
        if all(item["classification"] == "TEACHING_EDIT_POSSIBLE" for item in classified):
            return _result("SAME_SNAPSHOT_REPAIR", classified)
        return _result("STOP_UNSUPPORTED", classified)

    if route == "D2A_GENERAL_LEARNING":
        factuality = reports.get("factuality")
        beginner = reports.get("beginner")
        if factuality is None or beginner is None:
            return _result("REPORTS_MISSING")
        classified, has_blocker = _general_findings(
            status, {"factuality": factuality, "beginner": beginner},
            run_state_id=run_state_id, prerequisite_ids=prerequisite_ids,
        )
        if not classified:
            return _result("STOP_UNSUPPORTED")
        if any(item["classification"] == "STOP_UNSUPPORTED" for item in classified):
            return _result("STOP_UNSUPPORTED", classified)
        if any(item["classification"] == "UPSTREAM_REQUIRED" for item in classified):
            return _result("UPSTREAM_REQUIRED", classified)
        if not has_blocker and all(item["classification"] == "TEACHING_EDIT_POSSIBLE" for item in classified):
            return _result("SAME_SNAPSHOT_REPAIR", classified)
        return _result("STOP_UNSUPPORTED", classified)

    return _result("STOP_UNSUPPORTED")


def open_repair_attempt(row: Mapping[str, Any]) -> dict[str, Any]:
    """Append one empty immutable attempt record bound to the prior finalized review."""
    route_roles = {
        "PHASE6A_PROJECT_CLAIM": ("chapter_review_status", ("evidence_report", "beginner_report")),
        "D2A_GENERAL_LEARNING": ("general_review_status", ("factuality_report", "beginner_report")),
    }
    if (row.get("execution_state") != "BLOCKED_REVIEW" or row.get("route") not in route_roles
            or not row.get("attempts") or len(row["attempts"]) not in {1, 2}
            or row.get("attempt_id") != row["attempts"][-1]["attempt_id"]
            or row["attempts"][-1]["execution_state"] != "BLOCKED_REVIEW"):
        raise ValueError("REPAIR_LINEAGE_INVALID")
    status_role, report_roles = route_roles[row["route"]]
    predecessor = row["attempts"][-1]
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    for entry in predecessor["artifact_digests"]:
        by_role.setdefault(entry["role"], []).append(entry)
    status_entries = by_role.get(status_role, [])
    reports = [by_role.get(role, []) for role in report_roles]
    if len(status_entries) != 1 or any(len(entries) != 1 for entries in reports):
        raise ValueError("REPAIR_LINEAGE_INVALID")
    attempt_number = len(row["attempts"]) + 1
    attempt_id = f"attempt-{attempt_number:04d}"
    new_attempt = {
        "attempt_id": attempt_id, "execution_state": "REPAIR_READY", "disposition": "SAME_SNAPSHOT_REPAIR",
        "predecessor_attempt_id": predecessor["attempt_id"],
        "predecessor_review_status_sha256": status_entries[0]["sha256"],
        "predecessor_report_digests": [
            {"role": role, "sha256": entries[0]["sha256"]}
            for role, entries in zip(report_roles, reports, strict=True)
        ],
        "artifact_digests": [],
    }
    updated = copy.deepcopy(dict(row))
    updated["attempts"].append(new_attempt)
    updated["attempt_id"] = attempt_id
    updated["execution_state"] = "REPAIR_READY"
    updated["repair_disposition"] = "SAME_SNAPSHOT_REPAIR"
    return updated


__all__ = ["classify_repair_findings", "open_repair_attempt"]
