#!/usr/bin/env python3
"""Deterministic routing and artifact projection for an authenticated curriculum."""

from __future__ import annotations

import copy
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from phase4_graph import canonical_tuple_sha256
from phase4_claim_evidence import AuthenticatedClaimEvidenceContext
from phase6_chapter import _ReadInput, _authenticated_input_digest_records
from phase6_general_learning import Phase6GeneralLearningError, select_general_learning_unit


_PROJECT_REF_KEYS = ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")
_READINESS_VALUES = {"READY", "NO_QUALIFYING_SUPPORTED_CLAIM", "CLAIM_SCOPE_AMBIGUOUS"}
_EXPECTED_DIGEST_ROLES = {
    "phase4_base_graph", "phase4_base_evidence", "phase4_semantic_proposals",
    "phase4_claim_candidates", "phase4_claim_evidence", "phase4_claim_evidence_graph",
    "phase5_prerequisite_candidates", "phase5_prerequisite_graph",
    "phase5_curriculum_candidates", "phase5_curriculum",
}


class Phase6CurriculumRunError(ValueError):
    """A fixed, redacted run-plan validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _fail(code: str) -> None:
    raise Phase6CurriculumRunError(code)


def _project_refs_empty(unit: Mapping[str, Any]) -> bool:
    refs = unit.get("project_refs")
    if not isinstance(refs, Mapping) or set(refs) != set(_PROJECT_REF_KEYS):
        _fail("INPUT_INVALID")
    if any(not isinstance(refs[key], list) for key in _PROJECT_REF_KEYS):
        _fail("INPUT_INVALID")
    return all(not refs[key] for key in _PROJECT_REF_KEYS)


def _ordered_units(curriculum: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    units = curriculum.get("units")
    if not isinstance(units, list) or len(units) < 2:
        _fail("INPUT_INVALID")
    ordered: list[Mapping[str, Any]] = []
    prior_ids: set[str] = set()
    all_ids: set[str] = set()
    for position, unit in enumerate(units, start=1):
        if not isinstance(unit, Mapping):
            _fail("INPUT_INVALID")
        unit_id = unit.get("id")
        prerequisites = unit.get("prerequisite_ids")
        if not isinstance(unit_id, str) or unit_id in all_ids:
            _fail("CURRICULUM_DEPENDENCY_INVALID")
        if unit.get("order") != position:
            _fail("CURRICULUM_DEPENDENCY_INVALID")
        if not isinstance(prerequisites, list) or any(not isinstance(value, str) for value in prerequisites):
            _fail("CURRICULUM_DEPENDENCY_INVALID")
        if len(prerequisites) != len(set(prerequisites)) or any(value not in prior_ids for value in prerequisites):
            _fail("CURRICULUM_DEPENDENCY_INVALID")
        ordered.append(unit)
        prior_ids.add(unit_id)
        all_ids.add(unit_id)
    return ordered


def _general_route(
    curriculum: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> tuple[str, str, list[str]]:
    try:
        select_general_learning_unit(curriculum, unit["id"])
    except Phase6GeneralLearningError as exc:
        if exc.code in {"UNIT_METADATA_INVALID", "UNIT_ORIGIN_SCOPE_INVALID"}:
            return "BLOCKED_UNSUPPORTED", "BLOCKED", ["D2A_METADATA_INVALID"]
        _fail("INPUT_INVALID")
    if unit["origin"] == "CANDIDATE":
        return "D2A_GENERAL_LEARNING", "PLANNED", ["ROUTE_GENERAL_CANDIDATE"]
    return "D2A_GENERAL_LEARNING", "PLANNED", ["ROUTE_PREREQUISITE_PRIMER"]


def _base_route(
    curriculum: Mapping[str, Any],
    unit: Mapping[str, Any],
    readiness: Mapping[str, str],
) -> tuple[str, str, list[str]]:
    origin = unit.get("origin")
    scope = unit.get("scope")
    has_refs = not _project_refs_empty(unit)

    if origin == "CANDIDATE" and scope == "GENERAL_LEARNING":
        if has_refs:
            return "BLOCKED_UNSUPPORTED", "BLOCKED", ["GENERAL_PROJECT_REFERENCES_PRESENT"]
        return _general_route(curriculum, unit)

    if origin == "PREREQUISITE_PRIMER" and scope in {"GENERAL_LEARNING", "PROJECT_SPECIFIC"}:
        if has_refs:
            return "BLOCKED_UNSUPPORTED", "BLOCKED", ["PRIMER_PROJECT_REFERENCES_PRESENT"]
        return _general_route(curriculum, unit)

    if origin == "CANDIDATE" and scope == "PROJECT_SPECIFIC":
        if not has_refs:
            return "UPSTREAM_REQUIRED", "BLOCKED", ["PROJECT_REFERENCES_REQUIRED"]
        outcome = readiness.get(unit["id"])
        if outcome == "READY":
            return "PHASE6A_PROJECT_CLAIM", "PLANNED", ["ROUTE_PROJECT_CLAIM_SUPPORTED"]
        if outcome == "NO_QUALIFYING_SUPPORTED_CLAIM":
            return "UPSTREAM_REQUIRED", "BLOCKED", ["NO_QUALIFYING_SUPPORTED_CLAIM"]
        if outcome == "CLAIM_SCOPE_AMBIGUOUS":
            return "BLOCKED_UNSUPPORTED", "BLOCKED", ["CLAIM_SCOPE_AMBIGUOUS"]
        _fail("READINESS_INVALID")

    _fail("INPUT_INVALID")


def route_curriculum_units(
    curriculum: Mapping[str, Any],
    project_claim_readiness: Mapping[str, str],
    *,
    source_status: str,
) -> list[dict[str, Any]]:
    """Route every current Phase 5 unit without dropping or reordering it."""
    if source_status not in {"PASS", "PARTIAL"}:
        _fail("INPUT_INVALID")
    units = _ordered_units(curriculum)
    if not isinstance(project_claim_readiness, Mapping):
        _fail("READINESS_INVALID")

    expected_readiness_ids = {
        unit["id"] for unit in units
        if unit.get("origin") == "CANDIDATE"
        and unit.get("scope") == "PROJECT_SPECIFIC"
        and not _project_refs_empty(unit)
    }
    if set(project_claim_readiness) != expected_readiness_ids:
        _fail("READINESS_INVALID")
    if any(value not in _READINESS_VALUES for value in project_claim_readiness.values()):
        _fail("READINESS_INVALID")

    rows: list[dict[str, Any]] = []
    row_by_id: dict[str, dict[str, Any]] = {}
    for position, unit in enumerate(units, start=1):
        route, state, reasons = _base_route(curriculum, unit, project_claim_readiness)
        blocked_by = [
            prerequisite_id for prerequisite_id in unit["prerequisite_ids"]
            if row_by_id[prerequisite_id]["state"] == "BLOCKED"
        ]
        if blocked_by:
            state = "BLOCKED"
            reasons.append("PREREQUISITE_NOT_READY")
        if source_status == "PARTIAL":
            reasons.append("SOURCE_PARTIAL")
        row = {
            "position": position,
            "unit_identity": copy.deepcopy(dict(unit)),
            "route": route,
            "state": state,
            "reason_codes": sorted(set(reasons)),
            "blocked_by": blocked_by,
        }
        rows.append(row)
        row_by_id[unit["id"]] = row
    return rows


def build_curriculum_run_plan(
    authenticated: AuthenticatedClaimEvidenceContext,
    reads: Mapping[str, _ReadInput],
    prerequisite_graph: Mapping[str, Any],
    curriculum: Mapping[str, Any],
    project_claim_readiness: Mapping[str, str],
) -> dict[str, Any]:
    """Build and validate the immutable plan from one authenticated capture."""
    del prerequisite_graph  # _capture_bundle already reconstructed and cross-checked it.
    try:
        units = _ordered_units(curriculum)
        input_digests = _authenticated_input_digest_records(reads, authenticated)
        if (
            len(input_digests) != 10
            or {item["role"] for item in input_digests} != _EXPECTED_DIGEST_ROLES
            or [item["role"] for item in input_digests] != sorted(_EXPECTED_DIGEST_ROLES)
        ):
            _fail("INPUT_INVALID")
        source_metadata = copy.deepcopy(dict(curriculum["source_metadata"]))
        unknown_files = source_metadata["unknown_files"]
        if not isinstance(unknown_files, int) or isinstance(unknown_files, bool) or unknown_files < 0:
            _fail("INPUT_INVALID")
        if curriculum["snapshot_kind"] != source_metadata["snapshot_kind"]:
            _fail("INPUT_INVALID")
        rows = route_curriculum_units(
            curriculum, project_claim_readiness, source_status=curriculum["source_status"],
        )
        ordered_unit_ids = [unit["id"] for unit in units]
        plan = {
            "artifact_kind": "curriculum-run-plan",
            "schema_version": "1.0.0",
            "run_plan_id": "CURRICULUM-RUN-PLAN-" + canonical_tuple_sha256((
                curriculum["repository_revision"],
                curriculum["snapshot_kind"],
                curriculum["source_run_manifest_sha256"],
                reads["curriculum"].sha256,
                input_digests,
                ordered_unit_ids,
            )),
            "repository_revision": curriculum["repository_revision"],
            "snapshot_kind": curriculum["snapshot_kind"],
            "source_metadata": source_metadata,
            "source_status": curriculum["source_status"],
            "source_run_manifest_sha256": curriculum["source_run_manifest_sha256"],
            "unknown_files": unknown_files,
            "generated_at": curriculum["generated_at"],
            "curriculum_schema_version": curriculum["schema_version"],
            "curriculum_sha256": reads["curriculum"].sha256,
            "unit_count": len(units),
            "input_digests": input_digests,
            "units": rows,
        }
        validate_artifact(plan)
        return plan
    except Phase6CurriculumRunError:
        raise
    except (ArtifactValidationError, KeyError, TypeError, ValueError):
        _fail("PLAN_INVALID")
