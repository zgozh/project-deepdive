#!/usr/bin/env python3
"""Deterministic Phase 5B beginner curriculum outline projection."""

from __future__ import annotations

import copy
import heapq
import unicodedata
from collections import defaultdict
from typing import Any, Mapping

from artifact_contract import ArtifactValidationError, validate_artifact
from phase4_graph import Phase4GraphError, canonical_tuple_sha256, validate_phase4_graph_artifacts


class Phase5CurriculumError(ValueError):
    """A fixed, redacted Phase 5B validation or build failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


_PHASE5_INPUT_PROFILES = {
    "1.0.0": {
        "phase4_inputs": (
            ("evidence.json", "evidence", "1.2.0"),
            ("knowledge-graph.json", "knowledge-graph", "1.1.0"),
            ("semantic-proposals.json", "semantic-proposals", "1.0.0"),
        ),
        "graph_schema_version": "1.1.0",
        "evidence_schema_version": "1.2.0",
        "prerequisite_inputs": (
            ("prerequisite-candidates.json", "prerequisite-candidates", "1.0.0"),
            ("prerequisite-graph.json", "prerequisite-graph", "1.0.0"),
        ),
        "prerequisite_candidate_schema_version": "1.0.0",
        "prerequisite_graph_schema_version": "1.0.0",
        "curriculum_schema_version": "1.1.0",
    },
    "1.1.0": {
        "phase4_inputs": (
            ("evidence.json", "evidence", "1.4.0"),
            ("knowledge-graph.json", "knowledge-graph", "1.2.0"),
            ("semantic-proposals.json", "semantic-proposals", "1.1.0"),
        ),
        "graph_schema_version": "1.2.0",
        "evidence_schema_version": "1.4.0",
        "prerequisite_inputs": (
            ("prerequisite-candidates.json", "prerequisite-candidates", "1.1.0"),
            ("prerequisite-graph.json", "prerequisite-graph", "1.1.0"),
        ),
        "prerequisite_candidate_schema_version": "1.1.0",
        "prerequisite_graph_schema_version": "1.1.0",
        "curriculum_schema_version": "1.2.0",
    },
}
_STAGES = (
    "project-purpose", "business", "workflow", "architecture", "source",
    "mechanism", "failure", "vibecoding", "extension", "interview",
)
_DEEP_STAGES = frozenset({
    "architecture", "source", "mechanism", "failure", "vibecoding", "extension", "interview",
})
_SOURCE_REF_KEYS = ("graph_node_ids", "graph_edge_ids", "evidence_ids", "file_paths")
_CANDIDATE_SOURCE_FIELDS = ("repository_revision", "generated_at")


def _fail(code: str) -> None:
    raise Phase5CurriculumError(code)


def phase5_input_profile(candidates: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the exact registered Phase 5B input tuple for this candidate version."""
    if not isinstance(candidates, Mapping):
        _fail("INPUT_INVALID")
    profile = _PHASE5_INPUT_PROFILES.get(candidates.get("schema_version"))
    if profile is None:
        _fail("INPUT_INVALID")
    return profile


def _safe_source_path(value: str) -> bool:
    if not value or "\\" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} for part in parts)


def _snapshot_key(graph: Mapping[str, Any]) -> str:
    return canonical_tuple_sha256((
        graph["repository_revision"], graph["snapshot_kind"], graph["source_metadata"],
    ))


def _validate_input_records(
    rows: Any,
    expected: tuple[tuple[str, str, str], ...],
    *,
    code: str = "PROVENANCE_MISMATCH",
) -> list[Mapping[str, Any]]:
    if not isinstance(rows, list) or len(rows) != len(expected):
        _fail(code)
    normalized: list[Mapping[str, Any]] = []
    for row, (path, kind, version) in zip(rows, expected):
        if not isinstance(row, Mapping) or (
            row.get("path"), row.get("artifact_kind"), row.get("schema_version")
        ) != (path, kind, version):
            _fail(code)
        digest = row.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            _fail(code)
        normalized.append({
            "path": path,
            "artifact_kind": kind,
            "schema_version": version,
            "sha256": digest,
        })
    return normalized


def _phase4_indexes(
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
            _fail("INPUT_INVALID")
        files[path] = identifier
    return nodes, edges, evidence_items, files


def _normalize_project_refs(
    refs: Any,
    *,
    graph_nodes: Mapping[str, Mapping[str, Any]],
    graph_edges: Mapping[str, Mapping[str, Any]],
    evidence_items: Mapping[str, Mapping[str, Any]],
    files: Mapping[str, str],
) -> dict[str, list[str]]:
    if not isinstance(refs, Mapping):
        _fail("INPUT_INVALID")
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


def _refs_nonempty(refs: Mapping[str, list[str]]) -> bool:
    return any(refs[key] for key in _SOURCE_REF_KEYS)


def _prerequisite_indexes(
    prerequisite_graph: Mapping[str, Any],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]], dict[str, list[str]]]:
    nodes_by_key: dict[str, Mapping[str, Any]] = {}
    nodes_by_id: dict[str, Mapping[str, Any]] = {}
    for node in prerequisite_graph.get("nodes", []):
        key, identifier = node["concept_key"], node["id"]
        if key in nodes_by_key or identifier in nodes_by_id:
            _fail("INPUT_INVALID")
        nodes_by_key[key] = node
        nodes_by_id[identifier] = node

    requires: dict[str, list[str]] = {key: [] for key in nodes_by_key}
    edge_pairs: set[tuple[str, str]] = set()
    for edge in prerequisite_graph.get("edges", []):
        if edge.get("type") != "REQUIRES" or edge.get("epistemic_status") != "UNVERIFIED_TEACHING":
            _fail("INPUT_INVALID")
        dependent = nodes_by_id.get(edge.get("from"))
        prerequisite = nodes_by_id.get(edge.get("to"))
        if dependent is None or prerequisite is None:
            _fail("REFERENCE_MISSING")
        pair = (dependent["concept_key"], prerequisite["concept_key"])
        if pair[0] == pair[1] or pair in edge_pairs:
            _fail("INPUT_INVALID")
        edge_pairs.add(pair)
        requires[pair[0]].append(pair[1])
    for values in requires.values():
        values.sort()

    dependents: dict[str, list[str]] = defaultdict(list)
    indegree = {key: 0 for key in nodes_by_key}
    for dependent, prerequisites in requires.items():
        for prerequisite in prerequisites:
            dependents[prerequisite].append(dependent)
            indegree[dependent] += 1
    ready = [key for key, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    visited = 0
    while ready:
        prerequisite = heapq.heappop(ready)
        visited += 1
        for dependent in dependents[prerequisite]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    if visited != len(nodes_by_key):
        _fail("ORDER_INVALID")
    return nodes_by_key, nodes_by_id, requires


def _normalize_units(
    candidates: Mapping[str, Any],
    *,
    graph_nodes: Mapping[str, Mapping[str, Any]],
    graph_edges: Mapping[str, Mapping[str, Any]],
    evidence_items: Mapping[str, Mapping[str, Any]],
    files: Mapping[str, str],
    prerequisite_nodes: Mapping[str, Mapping[str, Any]],
    requires_by_concept: Mapping[str, list[str]],
    closure: Any,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    normalized_by_key: dict[str, dict[str, Any]] = {}
    for source in candidates.get("units", []):
        title = unicodedata.normalize("NFC", source["title"]).strip()
        if not title or len(title) > 160:
            _fail("INPUT_INVALID")
        refs = _normalize_project_refs(
            source["project_refs"], graph_nodes=graph_nodes, graph_edges=graph_edges,
            evidence_items=evidence_items, files=files,
        )
        scope = source["scope"]
        if scope == "PROJECT_SPECIFIC":
            if not _refs_nonempty(refs):
                _fail("REFERENCE_MISSING")
        elif scope == "GENERAL_LEARNING":
            if _refs_nonempty(refs):
                _fail("REFERENCE_MISSING")
        else:
            _fail("INPUT_INVALID")

        introduces = source["introduces_concept_keys"]
        required = source["requires_concept_keys"]
        if any(key not in prerequisite_nodes for key in introduces + required):
            _fail("REFERENCE_MISSING")
        normalized = {
            "unit_key": source["unit_key"],
            "stage": source["stage"],
            "title": title,
            "scope": scope,
            "introduces_concept_keys": sorted(set(introduces)),
            "requires_concept_keys": sorted(set(required)),
            "project_refs": refs,
        }
        previous = normalized_by_key.get(normalized["unit_key"])
        if previous is not None and previous != normalized:
            _fail("IDENTITY_CONFLICT")
        normalized_by_key[normalized["unit_key"]] = normalized

    units = list(normalized_by_key.values())
    units_by_key = {unit["unit_key"]: unit for unit in units}
    introducer_by_concept: dict[str, str] = {}
    for unit in units:
        for concept in unit["introduces_concept_keys"]:
            if concept in introducer_by_concept:
                _fail("IDENTITY_CONFLICT")
            introducer_by_concept[concept] = unit["unit_key"]

    for unit in units:
        unit_key = unit["unit_key"]
        introduced = set(unit["introduces_concept_keys"])
        required = set(unit["requires_concept_keys"])
        if introduced & required:
            _fail("ORDER_INVALID")
        for concept in introduced:
            if (closure(concept) - {concept}) & introduced:
                _fail("ORDER_INVALID")

    unit_prerequisites: dict[str, set[str]] = {unit["unit_key"]: set() for unit in units}
    for unit in units:
        unit_key = unit["unit_key"]
        required_before = set()
        for concept in unit["requires_concept_keys"]:
            required_before.update(closure(concept))
        for concept in unit["introduces_concept_keys"]:
            required_before.update(closure(concept) - {concept})
        for concept in required_before:
            dependency_unit = introducer_by_concept.get(concept)
            if dependency_unit is None:
                continue
            if dependency_unit == unit_key:
                _fail("ORDER_INVALID")
            dependency_stage = _STAGES.index(units_by_key[dependency_unit]["stage"])
            dependent_stage = _STAGES.index(unit["stage"])
            if dependency_stage > dependent_stage:
                _fail("ORDER_INVALID")
            unit_prerequisites[unit_key].add(dependency_unit)

    dependents: dict[str, list[str]] = defaultdict(list)
    indegree = {unit["unit_key"]: 0 for unit in units}
    for unit_key, prerequisites in unit_prerequisites.items():
        for prerequisite_unit in prerequisites:
            dependents[prerequisite_unit].append(unit_key)
            indegree[unit_key] += 1
    ready: list[tuple[int, str]] = []
    for unit in units:
        key = unit["unit_key"]
        if indegree[key] == 0:
            heapq.heappush(ready, (_STAGES.index(unit["stage"]), key))
    ordered_keys: list[str] = []
    while ready:
        _stage_rank, unit_key = heapq.heappop(ready)
        ordered_keys.append(unit_key)
        for dependent in dependents[unit_key]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, (_STAGES.index(units_by_key[dependent]["stage"]), dependent))
    if len(ordered_keys) != len(units):
        _fail("ORDER_INVALID")
    ordered = [units_by_key[key] for key in ordered_keys]
    if not any(unit["stage"] == "project-purpose" for unit in ordered):
        _fail("ORDER_INVALID")
    if not any(unit["stage"] == "workflow" for unit in ordered):
        _fail("ORDER_INVALID")
    first_deep = next((index for index, unit in enumerate(ordered) if unit["stage"] in _DEEP_STAGES), len(ordered))
    prefix_stages = {unit["stage"] for unit in ordered[:first_deep]}
    if "project-purpose" not in prefix_stages or "workflow" not in prefix_stages:
        _fail("ORDER_INVALID")
    return ordered, introducer_by_concept


def _topological_primer_keys(
    needed: set[str], requires_by_concept: Mapping[str, list[str]],
) -> list[str]:
    indegree = {key: 0 for key in needed}
    dependents: dict[str, list[str]] = defaultdict(list)
    for dependent in needed:
        for prerequisite in requires_by_concept[dependent]:
            if prerequisite in needed:
                dependents[prerequisite].append(dependent)
                indegree[dependent] += 1
    ready = [key for key, degree in indegree.items() if degree == 0]
    heapq.heapify(ready)
    ordered: list[str] = []
    while ready:
        prerequisite = heapq.heappop(ready)
        ordered.append(prerequisite)
        for dependent in dependents[prerequisite]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(ordered) != len(needed):
        _fail("ORDER_INVALID")
    return ordered


def _unit_id(snapshot_key: str, unit_key: str) -> str:
    return "CURRICULUM-UNIT-" + canonical_tuple_sha256(("curriculum-unit", snapshot_key, unit_key))


def _primer_id(snapshot_key: str, prerequisite_node_id: str) -> str:
    return "CURRICULUM-PRIMER-" + canonical_tuple_sha256(("curriculum-primer", snapshot_key, prerequisite_node_id))


def _output_unit(
    *,
    identifier: str,
    order: int,
    title: str,
    kind: str,
    scope: str,
    prerequisite_ids: list[str],
    introduces: list[str],
    requires: list[str],
    project_refs: Mapping[str, list[str]],
    origin: str,
    unit_key: str | None = None,
    primer_concept_key: str | None = None,
    prerequisite_node_id: str | None = None,
    concept_epistemic_status: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": identifier,
        "order": order,
        "title": title,
        "kind": kind,
        "scope": scope,
        "depth": 1,
        "content_status": "OUTLINE_ONLY",
        "epistemic_status": "UNVERIFIED_TEACHING",
        "prerequisite_ids": sorted(set(prerequisite_ids)),
        "introduces_concept_keys": sorted(introduces),
        "requires_concept_keys": sorted(set(requires)),
        "project_refs": {key: list(project_refs[key]) for key in _SOURCE_REF_KEYS},
        "origin": origin,
    }
    if unit_key is not None:
        result["unit_key"] = unit_key
    if primer_concept_key is not None:
        result["primer_concept_key"] = primer_concept_key
    if prerequisite_node_id is not None:
        result["prerequisite_node_id"] = prerequisite_node_id
    if concept_epistemic_status is not None:
        result["concept_epistemic_status"] = concept_epistemic_status
    return result


def project_curriculum_outline(
    candidates: Mapping[str, Any],
    *,
    graph: Mapping[str, Any],
    evidence: Mapping[str, Any],
    prerequisite_graph: Mapping[str, Any],
    candidate_sha256: str,
) -> dict[str, Any]:
    """Project unordered curriculum candidates over already authenticated inputs."""
    try:
        validate_artifact(dict(candidates))
        validate_artifact(dict(graph))
        validate_artifact(dict(evidence))
        validate_artifact(dict(prerequisite_graph))
        validate_phase4_graph_artifacts(graph, evidence)
    except (ArtifactValidationError, Phase4GraphError, TypeError, ValueError):
        _fail("INPUT_INVALID")
    profile = phase5_input_profile(candidates)
    if (
        candidates.get("artifact_kind") != "curriculum-candidates"
        or graph.get("artifact_kind") != "knowledge-graph"
        or evidence.get("artifact_kind") != "evidence"
        or prerequisite_graph.get("artifact_kind") != "prerequisite-graph"
        or graph.get("schema_version") != profile["graph_schema_version"]
        or evidence.get("schema_version") != profile["evidence_schema_version"]
        or prerequisite_graph.get("schema_version") != profile["prerequisite_graph_schema_version"]
    ):
        _fail("INPUT_INVALID")

    for field in _CANDIDATE_SOURCE_FIELDS:
        if candidates.get(field) != graph.get(field) or prerequisite_graph.get(field) != graph.get(field):
            _fail("PROVENANCE_MISMATCH")
    for field in ("snapshot_kind", "source_metadata"):
        if prerequisite_graph.get(field) != graph.get(field):
            _fail("PROVENANCE_MISMATCH")
    source_run = graph.get("source_run")
    if not isinstance(source_run, Mapping) or (
        prerequisite_graph.get("source_run_manifest_sha256") != source_run.get("manifest_sha256")
    ):
        _fail("PROVENANCE_MISMATCH")
    source_status = graph.get("status")
    if (
        source_status not in {"PASS", "PARTIAL"}
        or evidence.get("status") != source_status
        or prerequisite_graph.get("source_status") != source_status
    ):
        _fail("PROVENANCE_MISMATCH")
    if candidates.get("learner_profile", "BEGINNER") != "BEGINNER":
        _fail("PROFILE_UNSUPPORTED")

    phase4_inputs = _validate_input_records(candidates.get("phase4_inputs"), profile["phase4_inputs"])
    if phase4_inputs != prerequisite_graph.get("phase4_inputs"):
        _fail("PROVENANCE_MISMATCH")
    prerequisite_inputs = _validate_input_records(
        candidates.get("prerequisite_inputs"), profile["prerequisite_inputs"],
    )
    candidate_input = prerequisite_graph.get("candidate_input")
    if not isinstance(candidate_input, Mapping) or (
        candidate_input.get("artifact_kind") != "prerequisite-candidates"
        or candidate_input.get("schema_version") != profile["prerequisite_candidate_schema_version"]
        or candidate_input.get("sha256") != prerequisite_inputs[0]["sha256"]
    ):
        _fail("PROVENANCE_MISMATCH")
    if (
        not isinstance(candidate_sha256, str)
        or len(candidate_sha256) != 64
        or any(ch not in "0123456789abcdef" for ch in candidate_sha256)
    ):
        _fail("INPUT_INVALID")

    graph_nodes, graph_edges, evidence_items, files = _phase4_indexes(graph, evidence)
    prerequisite_nodes, prerequisite_nodes_by_id, requires_by_concept = _prerequisite_indexes(prerequisite_graph)
    closure_cache: dict[str, set[str]] = {}

    def closure(key: str) -> set[str]:
        if key not in prerequisite_nodes:
            _fail("REFERENCE_MISSING")
        if key in closure_cache:
            return closure_cache[key]
        active: set[str] = {key}
        stack: list[tuple[str, int]] = [(key, 0)]
        while stack:
            current, next_index = stack[-1]
            dependencies = requires_by_concept[current]
            if next_index < len(dependencies):
                dependency = dependencies[next_index]
                stack[-1] = (current, next_index + 1)
                if dependency in active:
                    _fail("ORDER_INVALID")
                if dependency not in closure_cache:
                    active.add(dependency)
                    stack.append((dependency, 0))
                continue
            resolved = {current}
            for dependency in dependencies:
                resolved.update(closure_cache[dependency])
            closure_cache[current] = resolved
            active.remove(current)
            stack.pop()
        return closure_cache[key]

    units, introducer_by_concept = _normalize_units(
        candidates,
        graph_nodes=graph_nodes,
        graph_edges=graph_edges,
        evidence_items=evidence_items,
        files=files,
        prerequisite_nodes=prerequisite_nodes,
        requires_by_concept=requires_by_concept,
        closure=closure,
    )

    snapshot_key = _snapshot_key(graph)
    output_units: list[dict[str, Any]] = []
    unit_ids: set[str] = set()
    introduced_unit_by_concept: dict[str, str] = {}
    for unit in units:
        introduced = set(unit["introduces_concept_keys"])
        needed: set[str] = set()
        for concept in unit["requires_concept_keys"]:
            needed.update(closure(concept))
        for concept in introduced:
            needed.update(closure(concept) - introduced)
        needed.difference_update(introduced_unit_by_concept)
        primer_keys = {
            concept for concept in needed
            if concept not in introduced_unit_by_concept and concept not in introducer_by_concept
        }
        # Candidate unit dependencies were included in the topological schedule; any such
        # concept that remains unresolved here is supplied by a just-in-time primer.
        for concept in needed - primer_keys:
            if concept not in introduced_unit_by_concept:
                _fail("ORDER_INVALID")

        for concept in _topological_primer_keys(primer_keys, requires_by_concept):
            node = prerequisite_nodes[concept]
            prerequisite_ids = []
            for dependency in requires_by_concept[concept]:
                dependency_id = introduced_unit_by_concept.get(dependency)
                if dependency_id is None:
                    _fail("ORDER_INVALID")
                prerequisite_ids.append(dependency_id)
            title = unicodedata.normalize("NFC", node["label"]).strip()
            if not title or len(title) > 200:
                _fail("INPUT_INVALID")
            identifier = _primer_id(snapshot_key, node["id"])
            if identifier in unit_ids:
                _fail("IDENTITY_CONFLICT")
            primer = _output_unit(
                identifier=identifier,
                order=len(output_units) + 1,
                title=title,
                kind="prerequisite",
                scope=node["scope"],
                prerequisite_ids=prerequisite_ids,
                introduces=[concept],
                requires=requires_by_concept[concept],
                project_refs={key: [] for key in _SOURCE_REF_KEYS},
                origin="PREREQUISITE_PRIMER",
                primer_concept_key=concept,
                prerequisite_node_id=node["id"],
                concept_epistemic_status=node["epistemic_status"],
            )
            output_units.append(primer)
            unit_ids.add(identifier)
            introduced_unit_by_concept[concept] = identifier

        direct_required = set(unit["requires_concept_keys"])
        for concept in unit["introduces_concept_keys"]:
            direct_required.update(requires_by_concept[concept])
        prerequisite_ids: list[str] = []
        for concept in direct_required:
            identifier = introduced_unit_by_concept.get(concept)
            if identifier is None:
                _fail("ORDER_INVALID")
            prerequisite_ids.append(identifier)

        identifier = _unit_id(snapshot_key, unit["unit_key"])
        if identifier in unit_ids:
            _fail("IDENTITY_CONFLICT")
        candidate_unit = _output_unit(
            identifier=identifier,
            order=len(output_units) + 1,
            title=unit["title"],
            kind=unit["stage"],
            scope=unit["scope"],
            prerequisite_ids=prerequisite_ids,
            introduces=unit["introduces_concept_keys"],
            requires=sorted(direct_required),
            project_refs=unit["project_refs"],
            origin="CANDIDATE",
            unit_key=unit["unit_key"],
        )
        output_units.append(candidate_unit)
        unit_ids.add(identifier)
        for concept in introduced:
            introduced_unit_by_concept[concept] = identifier

    result = {
        "artifact_kind": "curriculum",
        "schema_version": profile["curriculum_schema_version"],
        "repository_revision": graph["repository_revision"],
        "generated_at": graph["generated_at"],
        "snapshot_kind": graph["snapshot_kind"],
        "source_metadata": copy.deepcopy(graph["source_metadata"]),
        "source_status": source_status,
        "source_run_manifest_sha256": source_run["manifest_sha256"],
        "phase4_inputs": phase4_inputs,
        "prerequisite_inputs": prerequisite_inputs,
        "candidate_input": {
            "artifact_kind": "curriculum-candidates",
            "schema_version": candidates["schema_version"],
            "sha256": candidate_sha256,
        },
        "learner_profile": "BEGINNER",
        "status": "PARTIAL",
        "units": output_units,
    }
    try:
        validate_artifact(result)
    except ArtifactValidationError:
        _fail("INPUT_INVALID")
    _validate_output_order(result)
    return result


def _validate_output_order(curriculum: Mapping[str, Any]) -> None:
    units = curriculum.get("units")
    if not isinstance(units, list):
        _fail("INPUT_INVALID")
    ids: set[str] = set()
    introduced_concepts: set[str] = set()
    seen_stages: set[str] = set()
    for expected_order, unit in enumerate(units, 1):
        if unit.get("order") != expected_order or unit.get("id") in ids:
            _fail("ORDER_INVALID")
        if any(identifier not in ids for identifier in unit.get("prerequisite_ids", [])):
            _fail("ORDER_INVALID")
        if any(concept not in introduced_concepts for concept in unit.get("requires_concept_keys", [])):
            _fail("ORDER_INVALID")
        introduces = unit.get("introduces_concept_keys", [])
        if len(introduces) != len(set(introduces)) or introduced_concepts.intersection(introduces):
            _fail("ORDER_INVALID")
        if unit.get("kind") in _DEEP_STAGES and not {"project-purpose", "workflow"}.issubset(seen_stages):
            _fail("ORDER_INVALID")
        ids.add(unit["id"])
        introduced_concepts.update(introduces)
        if unit.get("kind") in {"project-purpose", "workflow"}:
            seen_stages.add(unit["kind"])
