#!/usr/bin/env python3
"""Deterministic projection and semantic checks for Phase 5A prerequisites."""

from __future__ import annotations

import copy
import unicodedata
from collections import defaultdict, deque
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from phase4_graph import canonical_tuple_sha256


class Phase5PrerequisiteError(ValueError):
    """A fixed, redacted Phase 5A validation failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_PHASE4_PROFILES = {
    "1.0.0": {
        "inputs": (
            ("evidence.json", "evidence", "1.2.0"),
            ("knowledge-graph.json", "knowledge-graph", "1.1.0"),
            ("semantic-proposals.json", "semantic-proposals", "1.0.0"),
        ),
        "graph_version": "1.1.0",
        "evidence_version": "1.2.0",
        "output_version": "1.0.0",
    },
    "1.1.0": {
        "inputs": (
            ("evidence.json", "evidence", "1.4.0"),
            ("knowledge-graph.json", "knowledge-graph", "1.2.0"),
            ("semantic-proposals.json", "semantic-proposals", "1.1.0"),
        ),
        "graph_version": "1.2.0",
        "evidence_version": "1.4.0",
        "output_version": "1.1.0",
    },
}
_NODE_TYPES = frozenset({
    "Concept", "Command", "LanguageFeature", "FrameworkMechanism", "InfrastructureConcept",
})
_SCOPES = frozenset({"PROJECT_SPECIFIC", "GENERAL_LEARNING"})
_SOURCE_REF_KEYS = ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")


def _fail(code: str) -> None:
    raise Phase5PrerequisiteError(code)


def _snapshot_key(graph: Mapping[str, Any]) -> str:
    return canonical_tuple_sha256((
        graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
    ))


def _safe_source_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _candidate_identity(candidate: Mapping[str, Any], graph: Mapping[str, Any], evidence: Mapping[str, Any]) -> None:
    fields = ("repository_revision", "generated_at", "snapshot_kind", "source_metadata", "source_run")
    if any(candidate.get(field) != graph.get(field) for field in fields):
        _fail("PROVENANCE_MISMATCH")
    if any(graph.get(field) != evidence.get(field) for field in fields):
        _fail("PROVENANCE_MISMATCH")
    source_status = graph.get("status")
    if source_status not in {"PASS", "PARTIAL"} or evidence.get("status") != source_status:
        _fail("PROVENANCE_MISMATCH")
    if candidate.get("source_status") != source_status:
        _fail("PROVENANCE_MISMATCH")
    source_run = graph.get("source_run")
    if not isinstance(source_run, Mapping) or source_run.get("artifact_kind") != "phase3-run":
        _fail("PROVENANCE_MISMATCH")
    if source_run.get("status") != source_status:
        _fail("PROVENANCE_MISMATCH")


def _phase4_profile(candidate: Mapping[str, Any]) -> Mapping[str, Any]:
    profile = _PHASE4_PROFILES.get(candidate.get("schema_version"))
    if profile is None:
        _fail("PROVENANCE_MISMATCH")
    return profile


def _phase4_input_identity(
    candidate: Mapping[str, Any],
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Mapping[str, Any]:
    profile = _phase4_profile(candidate)
    rows = candidate.get("phase4_inputs")
    expected = profile["inputs"]
    if not isinstance(rows, list) or len(rows) != len(expected):
        _fail("PROVENANCE_MISMATCH")
    actual = tuple(
        (row.get("path"), row.get("artifact_kind"), row.get("schema_version"))
        if isinstance(row, Mapping) else None
        for row in rows
    )
    if (
        actual != expected
        or graph.get("schema_version") != profile["graph_version"]
        or evidence.get("schema_version") != profile["evidence_version"]
    ):
        _fail("PROVENANCE_MISMATCH")
    return profile


def _build_reference_indexes(
    graph: Mapping[str, Any], evidence: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]], dict[str, str]]:
    nodes = {row["id"]: row for row in graph.get("nodes", [])}
    edges = {row["id"]: row for row in graph.get("edges", [])}
    evidence_items = {row["id"]: row for row in evidence.get("items", [])}
    files: dict[str, str] = {}
    for identifier, node in nodes.items():
        if node.get("type") != "File":
            continue
        properties = node.get("properties", {})
        path = properties.get("path") if isinstance(properties, Mapping) else None
        if not isinstance(path, str) or not _safe_source_path(path) or path in files:
            _fail("REFERENCE_MISSING")
        files[path] = identifier
    return nodes, edges, evidence_items, files


def _normalize_source_refs(
    refs: Mapping[str, Any],
    *,
    graph_nodes: Mapping[str, Mapping[str, Any]],
    graph_edges: Mapping[str, Mapping[str, Any]],
    evidence_items: Mapping[str, Mapping[str, Any]],
    files: Mapping[str, str],
) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key in _SOURCE_REF_KEYS:
        values = refs.get(key)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            _fail("INPUT_INVALID")
        if values != sorted(set(values)):
            _fail("INPUT_INVALID")
        normalized[key] = list(values)
    if any(identifier not in graph_nodes for identifier in normalized["graph_node_ids"]):
        _fail("REFERENCE_MISSING")
    if any(identifier not in graph_edges for identifier in normalized["graph_edge_ids"]):
        _fail("REFERENCE_MISSING")
    if any(identifier not in evidence_items for identifier in normalized["evidence_ids"]):
        _fail("REFERENCE_MISSING")
    for path in normalized["file_paths"]:
        if not _safe_source_path(path) or path not in files:
            _fail("REFERENCE_MISSING")
    return normalized


def _validate_node_refs(
    scope: str,
    epistemic_status: str,
    refs: Mapping[str, list[str]],
    *,
    graph_nodes: Mapping[str, Mapping[str, Any]],
    graph_edges: Mapping[str, Mapping[str, Any]],
    evidence_items: Mapping[str, Mapping[str, Any]],
) -> None:
    all_refs = [identifier for key in _SOURCE_REF_KEYS for identifier in refs[key]]
    if scope == "GENERAL_LEARNING":
        if all_refs or epistemic_status != "UNVERIFIED_TEACHING":
            _fail("REFERENCE_MISSING")
        return
    if scope != "PROJECT_SPECIFIC" or not all_refs:
        _fail("REFERENCE_MISSING")

    cited_items = [graph_nodes[key] for key in refs["graph_node_ids"]]
    cited_items.extend(graph_edges[key] for key in refs["graph_edge_ids"])
    linked_e6: set[str] = set()
    for item in cited_items:
        for evidence_id in item.get("evidence_ids", []):
            evidence_item = evidence_items.get(evidence_id)
            if evidence_item is not None and evidence_item.get("level") == "E6":
                linked_e6.add(evidence_id)

    cited_e6 = {
        evidence_id for evidence_id in refs["evidence_ids"]
        if evidence_items[evidence_id].get("level") == "E6"
    }
    if linked_e6 != cited_e6:
        _fail("E6_LINK_INVALID")
    expected_status = "E6_PROVISIONAL" if cited_e6 else "UNVERIFIED_TEACHING"
    if epistemic_status != expected_status:
        _fail("E6_LINK_INVALID")


def _node_identifier(snapshot_key: str, node: Mapping[str, Any]) -> str:
    digest = canonical_tuple_sha256((
        "prerequisite-node", snapshot_key, node["concept_key"], node["type"], node["scope"],
    ))
    return "PREREQ-NODE-" + digest


def _edge_identifier(snapshot_key: str, source: str, target: str) -> str:
    digest = canonical_tuple_sha256(("prerequisite-edge", snapshot_key, source, target))
    return "PREREQ-EDGE-" + digest


def _validate_dag(nodes: list[Mapping[str, Any]], edges: list[Mapping[str, Any]]) -> None:
    node_ids = {node["id"] for node in nodes}
    adjacency: dict[str, list[str]] = defaultdict(list)
    indegree = {identifier: 0 for identifier in node_ids}
    for edge in edges:
        source, target = edge["from"], edge["to"]
        if source not in node_ids or target not in node_ids:
            _fail("REFERENCE_MISSING")
        if source == target:
            _fail("GRAPH_INVALID")
        # B is a prerequisite of A, so traversal orders B before A.
        adjacency[target].append(source)
        indegree[source] += 1
    ready = deque(identifier for identifier, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        current = ready.popleft()
        visited += 1
        for dependent in adjacency[current]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
    if visited != len(node_ids):
        _fail("GRAPH_INVALID")


def project_prerequisite_graph(
    candidates: Mapping[str, Any],
    *,
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
    candidate_sha256: str,
) -> dict[str, Any]:
    """Project a candidate artifact over already authenticated Phase 4C values."""
    try:
        validate_artifact(dict(candidates))
        validate_artifact(dict(graph))
        validate_artifact(dict(evidence))
    except (ArtifactValidationError, TypeError, ValueError):
        _fail("INPUT_INVALID")
    if candidates.get("artifact_kind") != "prerequisite-candidates":
        _fail("INPUT_INVALID")
    _candidate_identity(candidates, graph, evidence)
    profile = _phase4_input_identity(candidates, graph, evidence)
    snapshot_key = _snapshot_key(graph)
    graph_nodes, graph_edges, evidence_items, files = _build_reference_indexes(graph, evidence)

    normalized_by_key: dict[str, dict[str, Any]] = {}
    normalized_by_id: dict[str, Mapping[str, Any]] = {}
    nodes: list[dict[str, Any]] = []
    for source in candidates.get("nodes", []):
        label = unicodedata.normalize("NFC", source["label"]).strip()
        if not label:
            _fail("INPUT_INVALID")
        refs = _normalize_source_refs(
            source["source_refs"], graph_nodes=graph_nodes, graph_edges=graph_edges,
            evidence_items=evidence_items, files=files,
        )
        node = {
            "id": source["id"],
            "concept_key": source["concept_key"],
            "type": source["type"],
            "label": label,
            "scope": source["scope"],
            "epistemic_status": source["epistemic_status"],
            "source_refs": refs,
        }
        expected_id = _node_identifier(snapshot_key, node)
        if node["id"] != expected_id:
            _fail("IDENTITY_CONFLICT")
        if node["type"] not in _NODE_TYPES or node["scope"] not in _SCOPES:
            _fail("INPUT_INVALID")
        _validate_node_refs(
            node["scope"], node["epistemic_status"], refs,
            graph_nodes=graph_nodes, graph_edges=graph_edges, evidence_items=evidence_items,
        )
        previous = normalized_by_key.get(node["concept_key"])
        if previous is not None:
            if previous != node:
                _fail("IDENTITY_CONFLICT")
            continue
        collision = normalized_by_id.get(node["id"])
        if collision is not None and collision != node:
            _fail("IDENTITY_CONFLICT")
        normalized_by_key[node["concept_key"]] = node
        normalized_by_id[node["id"]] = node
        nodes.append(node)

    normalized_edges: dict[tuple[str, str], dict[str, Any]] = {}
    edge_ids: dict[str, Mapping[str, Any]] = {}
    for source in candidates.get("edges", []):
        edge = copy.deepcopy(source)
        if edge["type"] != "REQUIRES" or edge["epistemic_status"] != "UNVERIFIED_TEACHING":
            _fail("INPUT_INVALID")
        if edge["from"] not in normalized_by_id or edge["to"] not in normalized_by_id:
            _fail("REFERENCE_MISSING")
        if edge["from"] == edge["to"]:
            _fail("GRAPH_INVALID")
        if edge["id"] != _edge_identifier(snapshot_key, edge["from"], edge["to"]):
            _fail("IDENTITY_CONFLICT")
        pair = (edge["from"], edge["to"])
        previous = normalized_edges.get(pair)
        if previous is not None:
            if previous != edge:
                _fail("IDENTITY_CONFLICT")
            continue
        collision = edge_ids.get(edge["id"])
        if collision is not None and collision != edge:
            _fail("IDENTITY_CONFLICT")
        normalized_edges[pair] = edge
        edge_ids[edge["id"]] = edge

    nodes.sort(key=lambda row: (row["type"], row["id"]))
    edges = sorted(normalized_edges.values(), key=lambda row: (row["type"], row["id"]))
    _validate_dag(nodes, edges)

    source_run = graph["source_run"]
    output = {
        "artifact_kind": "prerequisite-graph",
        "schema_version": profile["output_version"],
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_status": graph["status"],
        "source_run_manifest_sha256": source_run["manifest_sha256"],
        "phase4_inputs": copy.deepcopy(candidates["phase4_inputs"]),
        "candidate_input": {
            "artifact_kind": "prerequisite-candidates",
            "schema_version": candidates["schema_version"],
            "sha256": candidate_sha256,
        },
        "nodes": nodes,
        "edges": edges,
    }
    try:
        validate_artifact(output)
    except (ArtifactValidationError, TypeError, ValueError):
        _fail("INPUT_INVALID")
    return output


__all__ = ["Phase5PrerequisiteError", "project_prerequisite_graph"]
