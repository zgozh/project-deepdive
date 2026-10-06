#!/usr/bin/env python3
"""Pure projection and validation for the compact Phase 4E2 overlay."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from artifact_contract import ArtifactValidationError, validate_artifact
from phase4_graph import canonical_tuple_sha256


ARTIFACT_KIND = "claim-evidence-graph"
SCHEMA_VERSION = "1.0.0"
SCHEMA_VERSION_DOCUMENTATION = "1.1.0"
_PACKAGE_PATHS = {
    "knowledge-graph": "knowledge-graph.json",
    "evidence": "evidence.json",
    "semantic-proposals": "semantic-proposals.json",
}
_PROFILES = {
    ("1.1.0", "1.1.0"): {
        "overlay_version": SCHEMA_VERSION,
        "inputs": {
            "base_graph": ("knowledge-graph", "1.1.0"),
            "base_evidence": ("evidence", "1.2.0"),
            "semantic_proposals": ("semantic-proposals", "1.0.0"),
            "claim_candidates": ("claim-candidates", "1.1.0"),
            "claim_evidence": ("claim-evidence", "1.1.0"),
        },
    },
    ("1.2.0", "1.2.0"): {
        "overlay_version": SCHEMA_VERSION_DOCUMENTATION,
        "inputs": {
            "base_graph": ("knowledge-graph", "1.2.0"),
            "base_evidence": ("evidence", "1.4.0"),
            "semantic_proposals": ("semantic-proposals", "1.1.0"),
            "claim_candidates": ("claim-candidates", "1.2.0"),
            "claim_evidence": ("claim-evidence", "1.2.0"),
        },
    },
}
_IDENTITY_FIELDS = (
    "repository_revision",
    "generated_at",
    "snapshot_kind",
    "source_metadata",
    "source_run",
)
_EVIDENCE_LEVELS = frozenset({"E0", "E1", "E2", "E3", "E4", "E5", "E6"})


class Phase4ClaimGraphError(ValueError):
    """An authenticated claim/evidence overlay failed its semantic contract."""


def _validate_projection_inputs(
    graph: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    report: Mapping[str, Any],
    input_digests: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    if graph.get("artifact_kind") != "knowledge-graph":
        raise Phase4ClaimGraphError("unsupported base graph")
    if report.get("artifact_kind") != "claim-evidence":
        raise Phase4ClaimGraphError("unsupported audit report")
    profile = _PROFILES.get((graph.get("schema_version"), report.get("schema_version")))
    if profile is None:
        raise Phase4ClaimGraphError("unsupported artifact version pair")
    if any(report.get(field) != graph.get(field) for field in _IDENTITY_FIELDS):
        raise Phase4ClaimGraphError("source identity mismatch")
    if graph.get("status") not in {"PASS", "PARTIAL"}:
        raise Phase4ClaimGraphError("invalid source status")
    if report.get("audit_status") not in {"PASS", "PARTIAL", "FAIL"}:
        raise Phase4ClaimGraphError("invalid audit status")

    records_by_role: dict[str, dict[str, str]] = {}
    expected_inputs = profile["inputs"]
    for record in input_digests:
        if not isinstance(record, Mapping) or set(record) != {"role", "artifact_kind", "schema_version", "sha256"}:
            raise Phase4ClaimGraphError("invalid input digest record")
        role = record.get("role")
        expected = expected_inputs.get(role)
        if expected is None or role in records_by_role:
            raise Phase4ClaimGraphError("invalid input digest role")
        if (record.get("artifact_kind"), record.get("schema_version")) != expected:
            raise Phase4ClaimGraphError("input digest identity mismatch")
        digest = record.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise Phase4ClaimGraphError("invalid input digest")
        records_by_role[role] = dict(record)
    if set(records_by_role) != set(expected_inputs):
        raise Phase4ClaimGraphError("incomplete input digest set")

    phase4_inputs = report.get("phase4_inputs")
    if not isinstance(phase4_inputs, list) or len(phase4_inputs) != 3:
        raise Phase4ClaimGraphError("invalid report input set")
    report_inputs_by_kind: dict[str, Mapping[str, Any]] = {}
    expected_by_kind = {
        expected_inputs[role][0]: expected_inputs[role][1]
        for role in ("base_graph", "base_evidence", "semantic_proposals")
    }
    for record in phase4_inputs:
        if not isinstance(record, Mapping) or set(record) != {"path", "artifact_kind", "schema_version", "sha256"}:
            raise Phase4ClaimGraphError("invalid report input record")
        kind = record.get("artifact_kind")
        if kind not in expected_by_kind or kind in report_inputs_by_kind:
            raise Phase4ClaimGraphError("invalid report input kind")
        if (record.get("path"), record.get("schema_version")) != (
            _PACKAGE_PATHS[kind], expected_by_kind[kind],
        ):
            raise Phase4ClaimGraphError("report input identity mismatch")
        digest = record.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise Phase4ClaimGraphError("invalid report input digest")
        report_inputs_by_kind[kind] = record
    if set(report_inputs_by_kind) != set(expected_by_kind):
        raise Phase4ClaimGraphError("incomplete report input set")
    if phase4_inputs != sorted(phase4_inputs, key=lambda row: (row["artifact_kind"], row["path"])):
        raise Phase4ClaimGraphError("report input set is not canonical")
    for role in ("base_graph", "base_evidence", "semantic_proposals"):
        kind, _version = expected_inputs[role]
        if records_by_role[role]["sha256"] != report_inputs_by_kind[kind]["sha256"]:
            raise Phase4ClaimGraphError("report and overlay input digests disagree")
    return [records_by_role[role] for role in sorted(records_by_role)]


def _build_claim_evidence_graph_4c(
    graph: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    report: Mapping[str, Any],
    input_digests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    digests = _validate_projection_inputs(graph, evidence_by_id, report, input_digests)
    profile = _PROFILES[(graph["schema_version"], report["schema_version"])]
    snapshot_key = canonical_tuple_sha256((
        graph["repository_revision"],
        graph["snapshot_kind"],
        graph["source_metadata"],
    ))
    claim_nodes: dict[str, dict[str, Any]] = {}
    evidence_nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    claims = report.get("claims")
    if not isinstance(claims, list):
        raise Phase4ClaimGraphError("invalid claim matrix")
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise Phase4ClaimGraphError("invalid claim row")
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or claim_id in claim_nodes:
            raise Phase4ClaimGraphError("duplicate or invalid claim id")
        citation_results = claim.get("citation_results")
        evidence_ids = claim.get("evidence_ids")
        if not isinstance(citation_results, list) or not isinstance(evidence_ids, list):
            raise Phase4ClaimGraphError("invalid claim references")
        evidence_resolution: dict[str, bool] = {}
        for result in citation_results:
            if not isinstance(result, Mapping) or result.get("kind") != "evidence":
                continue
            evidence_id = result.get("value")
            resolved = result.get("resolved")
            if not isinstance(evidence_id, str) or not isinstance(resolved, bool):
                raise Phase4ClaimGraphError("invalid evidence citation result")
            previous = evidence_resolution.get(evidence_id)
            if previous is not None and previous != resolved:
                raise Phase4ClaimGraphError("conflicting evidence citation results")
            evidence_resolution[evidence_id] = resolved
        if set(evidence_resolution) != set(evidence_ids):
            raise Phase4ClaimGraphError("citation results do not match claim evidence ids")

        claim_nodes[claim_id] = {
            "id": claim_id,
            "type": "Claim",
            "label": claim["text"],
            "properties": {
                "category": claim["category"],
                "critical": claim["critical"],
                "disposition": claim["disposition"],
                "reason_codes": list(claim["reason_codes"]),
                "cited_evidence_ids": list(evidence_ids),
            },
        }

        for evidence_id in set(evidence_ids):
            evidence_item = evidence_by_id.get(evidence_id)
            if evidence_item is None:
                if evidence_resolution[evidence_id]:
                    raise Phase4ClaimGraphError("resolved evidence is absent from source ledger")
                continue
            if not evidence_resolution[evidence_id] or evidence_item.get("id") != evidence_id:
                raise Phase4ClaimGraphError("evidence resolution disagrees with source ledger")
            level = evidence_item.get("level")
            if level not in _EVIDENCE_LEVELS:
                raise Phase4ClaimGraphError("invalid evidence level")
            evidence_nodes[evidence_id] = {
                "id": evidence_id,
                "type": "Evidence",
                "label": level,
                "properties": {"level": level, "provisional": level == "E6"},
            }
            pair = (claim_id, evidence_id)
            edge_digest = canonical_tuple_sha256(("claim-evidence", snapshot_key, claim_id, evidence_id))
            edges[pair] = {
                "id": "CLAIM-EVIDENCE-" + edge_digest,
                "type": "EVIDENCED_BY",
                "from": claim_id,
                "to": evidence_id,
            }

    source_run = graph["source_run"]
    overlay = {
        "artifact_kind": ARTIFACT_KIND,
        "schema_version": profile["overlay_version"],
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "status": graph["status"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": deepcopy(graph["source_metadata"]),
        "source_run": {
            "artifact_kind": source_run["artifact_kind"],
            "schema_version": source_run["schema_version"],
            "manifest_sha256": source_run["manifest_sha256"],
            "status": source_run["status"],
            "members": sorted(
                (deepcopy(member) for member in source_run["members"]),
                key=lambda member: member["path"],
            ),
        },
        "audit_status": report["audit_status"],
        "input_digests": digests,
        "nodes": sorted((*claim_nodes.values(), *evidence_nodes.values()), key=lambda node: node["id"]),
        "edges": sorted(edges.values(), key=lambda edge: edge["id"]),
    }
    if profile["overlay_version"] == SCHEMA_VERSION_DOCUMENTATION:
        overlay["source_limits"] = list(report["source_limits"])
    validate_artifact(overlay)
    return overlay


def project_claim_evidence_graph_4c(
    graph: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    report: Mapping[str, Any],
    input_digests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Project only audited claims and their resolved explicit evidence citations."""
    try:
        return _build_claim_evidence_graph_4c(graph, evidence_by_id, report, input_digests)
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, Phase4ClaimGraphError):
            raise
        raise Phase4ClaimGraphError("invalid projection input") from exc


def validate_claim_evidence_graph_4c(
    artifact: Mapping[str, Any],
    graph: Mapping[str, Any],
    evidence_by_id: Mapping[str, Mapping[str, Any]],
    report: Mapping[str, Any],
    input_digests: Sequence[Mapping[str, Any]],
) -> Mapping[str, Any]:
    """Validate schema plus exact matrix, ledger, digest, and edge coverage."""
    try:
        validate_artifact(artifact)
        expected = _build_claim_evidence_graph_4c(graph, evidence_by_id, report, input_digests)
        if artifact != expected:
            raise Phase4ClaimGraphError("overlay does not match authenticated inputs")
        return artifact
    except (ArtifactValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, Phase4ClaimGraphError):
            raise
        raise Phase4ClaimGraphError("invalid overlay") from exc


__all__ = [
    "ARTIFACT_KIND",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_DOCUMENTATION",
    "Phase4ClaimGraphError",
    "project_claim_evidence_graph_4c",
    "validate_claim_evidence_graph_4c",
]
